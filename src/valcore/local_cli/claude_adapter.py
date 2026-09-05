"""Adapter for the Claude Code CLI (`claude`) in non-interactive/headless mode."""

import json
from pathlib import Path

from pydantic_ai.usage import RequestUsage


class ClaudeCliAdapter:
    """Builds a `claude -p ...` invocation and parses its `--output-format json` envelope."""

    cli_name = "claude"
    binary = "claude"

    def build_invocation(
        self, *, prompt: str, instructions: str, model_name: str, run_dir: Path
    ) -> list[str]:
        return [
            self.binary,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--model",
            model_name,
            "--system-prompt",
            instructions,
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
