"""Adapter for the Codex CLI (`codex exec`) in non-interactive mode."""

import json
from pathlib import Path

from pydantic_ai.usage import RequestUsage


class CodexCliAdapter:
    """Builds a `codex exec --json ...` invocation and parses its JSONL event stream."""

    cli_name = "codex"
    binary = "codex"

    def build_invocation(
        self, *, prompt: str, instructions: str, model_name: str, run_dir: Path
    ) -> list[str]:
        full_prompt = f"{instructions}\n\n{prompt}" if instructions else prompt
        return [self.binary, "exec", "--json", "--model", model_name, full_prompt]

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
