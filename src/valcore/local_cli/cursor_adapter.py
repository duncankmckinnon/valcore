"""Adapter for the Cursor CLI (`cursor-agent`) in non-interactive mode."""

import json
from pathlib import Path

from pydantic_ai.usage import RequestUsage


class CursorCliAdapter:
    """Builds a `cursor-agent -p ...` invocation and parses its `--output-format json` envelope.

    `--trust` is required on every call: `cursor-agent` refuses to run non-interactively in
    a directory it has not seen before, and every call here uses a fresh temp `--workspace`.

    ``-p`` is a boolean flag, so the prompt is a positional argument: it goes last, behind a
    bare ``--``, or a prompt starting with ``-`` (a markdown bullet, a ``---`` rule) is parsed
    as an unknown option and the call fails.

    ``--mode ask`` is the tightest restriction ``cursor-agent`` offers -- a read-only Q&A
    mode that makes no edits, though it can still read files. Row content is untrusted
    third-party text, and a prompt-in/JSON-out judge needs neither; cursor-agent has no
    flag that disables tools outright the way ``claude --tools ""`` does.
    """

    cli_name = "cursor"
    binary = "cursor-agent"

    def build_invocation(self, *, prompt: str, instructions: str, run_dir: Path) -> list[str]:
        full_prompt = f"{instructions}\n\n{prompt}" if instructions else prompt
        return [
            self.binary,
            "-p",
            "--output-format",
            "json",
            "--workspace",
            str(run_dir),
            "--trust",
            "--mode",
            "ask",
            "--",
            full_prompt,
        ]

    def parse_output(self, stdout: str) -> tuple[str, RequestUsage]:
        envelope = json.loads(stdout)
        if envelope.get("is_error"):
            raise RuntimeError(f"cursor-agent reported an error: {envelope}")
        usage_raw = envelope.get("usage") or {}
        usage = RequestUsage(
            input_tokens=usage_raw.get("inputTokens", 0),
            output_tokens=usage_raw.get("outputTokens", 0),
            cache_read_tokens=usage_raw.get("cacheReadTokens", 0),
            cache_write_tokens=usage_raw.get("cacheWriteTokens", 0),
        )
        return envelope["result"], usage
