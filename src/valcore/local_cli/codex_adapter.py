"""Adapter for the Codex CLI (`codex exec`) in non-interactive mode."""

import json
from pathlib import Path

from pydantic_ai.usage import RequestUsage


class CodexCliAdapter:
    """Builds a `codex exec --json ...` invocation and parses its JSONL event stream.

    The prompt is a positional argument, so it goes last behind a bare ``--``, or a prompt
    starting with ``-`` (a markdown bullet, a ``---`` rule) is parsed as an unknown option
    and the call fails.

    ``--sandbox read-only`` is the tightest restriction ``codex exec`` offers -- it denies
    writes and network access, though shell commands and file reads still run. Row content
    is untrusted third-party text, and a prompt-in/JSON-out judge needs none of it; codex
    has no flag that disables tools outright the way ``claude --tools ""`` does.
    """

    cli_name = "codex"
    binary = "codex"

    def build_invocation(
        self, *, prompt: str, instructions: str, model_name: str, run_dir: Path
    ) -> list[str]:
        full_prompt = f"{instructions}\n\n{prompt}" if instructions else prompt
        return [
            self.binary,
            "exec",
            "--json",
            "--model",
            model_name,
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--",
            full_prompt,
        ]

    def parse_output(self, stdout: str) -> tuple[str, RequestUsage]:
        text: str | None = None
        usage = RequestUsage()
        for line in stdout.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("type") == "item.completed":
                item = event.get("item") or {}
                if item.get("type") == "agent_message":
                    text = item.get("text")
            elif event.get("type") == "turn.completed":
                usage_raw = event.get("usage") or {}
                usage = RequestUsage(
                    input_tokens=usage_raw.get("input_tokens", 0),
                    output_tokens=usage_raw.get("output_tokens", 0),
                    cache_read_tokens=usage_raw.get("cached_input_tokens", 0),
                    cache_write_tokens=usage_raw.get("cache_write_input_tokens", 0),
                )
        if text is None:
            raise RuntimeError(f"codex produced no agent_message: {stdout[:2000]!r}")
        return text, usage
