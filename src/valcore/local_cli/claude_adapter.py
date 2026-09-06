"""Adapter for the Claude Code CLI (`claude`) in non-interactive/headless mode."""

import json
from pathlib import Path

from pydantic_ai.usage import RequestUsage


class ClaudeCliAdapter:
    """Builds a `claude -p ...` invocation and parses its `--output-format json` envelope.

    ``-p``/``--print`` is a boolean flag, so the prompt is a positional argument: it goes
    last, behind a bare ``--``, or a prompt starting with ``-`` (a markdown bullet, a ``---``
    rule) is parsed as an unknown option and the call fails.

    ``--tools ""`` disables every built-in tool: row content is untrusted third-party text
    and a prompt-in/JSON-out judge has no use for the CLI's file or shell tools. It has to
    be ``--tools``, not ``--allowedTools``: the latter is a permission allowlist for tools
    that would otherwise prompt, and read-only tools like ``Read`` are permitted regardless,
    so ``--allowedTools ""`` leaves the filesystem reachable.
    """

    cli_name = "claude"
    binary = "claude"

    def build_invocation(self, *, prompt: str, instructions: str, run_dir: Path) -> list[str]:
        return [
            self.binary,
            "-p",
            "--output-format",
            "json",
            "--system-prompt",
            instructions,
            "--tools",
            "",
            "--",
            prompt,
        ]

    def parse_output(self, stdout: str) -> tuple[str, RequestUsage]:
        envelope = json.loads(stdout)
        if envelope.get("is_error"):
            raise RuntimeError(f"claude reported an error: {envelope}")
        usage_raw = envelope.get("usage") or {}
        usage = RequestUsage(
            input_tokens=usage_raw.get("input_tokens", 0),
            output_tokens=usage_raw.get("output_tokens", 0),
            cache_read_tokens=usage_raw.get("cache_read_input_tokens", 0),
            cache_write_tokens=usage_raw.get("cache_creation_input_tokens", 0),
        )
        return envelope["result"], usage
