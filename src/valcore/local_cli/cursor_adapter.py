"""Adapter for the Cursor CLI (`cursor-agent`) in non-interactive mode."""

import json
from pathlib import Path

from pydantic_ai.usage import RequestUsage


class CursorCliAdapter:
    """Builds a `cursor-agent -p ...` invocation and parses its `--output-format json` envelope.

    `--trust` is required on every call: `cursor-agent` refuses to run non-interactively in
    a directory it has not seen before, and every call here uses a fresh temp `--workspace`.
    """

    cli_name = "cursor"
    binary = "cursor-agent"

    def build_invocation(
        self, *, prompt: str, instructions: str, model_name: str, run_dir: Path
    ) -> list[str]:
        full_prompt = f"{instructions}\n\n{prompt}" if instructions else prompt
        return [
            self.binary,
            "-p",
            full_prompt,
            "--output-format",
            "json",
            "--model",
            model_name,
            "--workspace",
            str(run_dir),
            "--trust",
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
