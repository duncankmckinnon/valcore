"""A pydantic_ai Model that delegates one Agent turn to a locally installed agent CLI.

See docs/superpowers/specs/2026-09-04-local-cli-model-design.md for the design this
implements: every Agent turn is exactly one non-interactive CLI subprocess call, prompt
in, JSON out -- no tool-call round-tripping.
"""

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage


class CliAdapter(Protocol):
    """Builds a non-interactive subprocess invocation for one locally installed agent CLI,
    and parses that CLI's own output envelope back into plain text plus token usage."""

    cli_name: str

    def build_invocation(self, *, prompt: str, instructions: str, run_dir: Path) -> list[str]:
        """Return the argv to run this CLI non-interactively for one prompt/instructions pair.

        `run_dir` is a fresh temporary directory scoped to this call alone, for an adapter
        that needs to write its own config there (e.g. a workspace-scoped trust marker).
        """
        ...

    def parse_output(self, stdout: str) -> tuple[str, RequestUsage]:
        """Parse this CLI's captured stdout into (final answer text, token usage).

        Raises if the envelope reports an error or is missing the final answer.
        """
        ...


def _render_user_prompt(messages: list[ModelMessage]) -> str:
    """Return the text of the most recent user prompt.

    Raises if the most recent request has no user-prompt part, or if that part is not
    plain text -- CliBridgeModel does not support multimodal prompts.
    """
    for message in reversed(messages):
        if not isinstance(message, ModelRequest):
            continue
        texts = [p.content for p in message.parts if isinstance(p, UserPromptPart)]
        if texts:
            if not all(isinstance(t, str) for t in texts):
                raise TypeError("CliBridgeModel only supports plain-text user prompts.")
            return "\n".join(texts)  # type: ignore[arg-type]
    raise ValueError("No user prompt found in messages.")


def _extract_retry_content(messages: list[ModelMessage]) -> str | None:
    """Extract retry prompt content from the most recent ModelRequest, if present.

    Returns the rendered retry content if a RetryPromptPart exists, or None if there is no retry.
    The retry content may be a string (pre-formatted error message) or a list of error dicts.
    """
    for message in reversed(messages):
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, RetryPromptPart):
                content = part.content
                if isinstance(content, str):
                    return content
                # content is a list of error dicts (pydantic validation errors)
                error_lines = []
                for error in content:
                    loc = ".".join(str(x) for x in error.get("loc", ()))
                    msg = error.get("msg", "Unknown error")
                    error_lines.append(f"  - {loc}: {msg}" if loc else f"  - {msg}")
                return "Validation errors in your previous response:\n" + "\n".join(error_lines)
        # Only check the most recent ModelRequest
        break
    return None


def _strip_code_fence(text: str) -> str:
    """Strip a wrapping ``` / ```json fence, if present."""
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse text as a JSON object, tolerating a wrapping markdown code fence."""
    stripped = text.strip()
    for candidate in (stripped, _strip_code_fence(stripped)):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise RuntimeError(
        f"CLI did not return a JSON object matching the output schema: {text[:2000]!r}"
    )


class CliBridgeModel(Model):
    """Delegates a single Agent turn to a non-interactive agent CLI: prompt in, JSON out."""

    def __init__(self, adapter: CliAdapter) -> None:
        self._adapter = adapter
        super().__init__()

    @property
    def model_name(self) -> str:
        return "default"

    @property
    def system(self) -> str:
        return f"local-cli-{self._adapter.cli_name}"

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        _model_settings, params = self.prepare_request(model_settings, model_request_parameters)
        if not params.output_tools:
            raise ValueError("CliBridgeModel requires an Agent with output_type set.")
        output_tool = params.output_tools[0]

        instruction_parts = self._get_instruction_parts(messages, params)
        instructions = (
            "\n\n".join(p.content for p in instruction_parts) if instruction_parts else ""
        )
        schema_directive = (
            "Respond with ONLY a single JSON object matching this JSON Schema, and nothing "
            "else (no markdown fences, no explanation):\n"
            f"{json.dumps(output_tool.parameters_json_schema)}"
        )
        full_instructions = (
            f"{instructions}\n\n{schema_directive}" if instructions else schema_directive
        )

        # If there's a retry, incorporate the retry content into the instructions
        retry_content = _extract_retry_content(messages)
        if retry_content:
            full_instructions = f"{full_instructions}\n\nYour previous response was invalid:\n{retry_content}\n\nPlease correct it and respond again with only the JSON object."

        prompt = _render_user_prompt(messages)

        with tempfile.TemporaryDirectory(prefix="valcore-local-cli-") as run_dir_str:
            run_dir = Path(run_dir_str)
            argv = self._adapter.build_invocation(
                prompt=prompt,
                instructions=full_instructions,
                run_dir=run_dir,
            )
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=run_dir,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                # Both streams matter: claude puts the human-readable cause of a failure
                # (a bad model name, say) on stdout inside its JSON envelope while stderr
                # carries only a terse code, so reporting stderr alone loses the diagnostic.
                raise RuntimeError(
                    f"{self._adapter.cli_name} exited with code {proc.returncode}: "
                    f"stdout={stdout.decode(errors='replace')[:2000]!r} "
                    f"stderr={stderr.decode(errors='replace')[:2000]!r}"
                )

        text, usage = self._adapter.parse_output(stdout.decode(errors="replace"))
        result_args = _extract_json_object(text)

        return ModelResponse(
            parts=[
                ToolCallPart(tool_name=output_tool.name, args=result_args, tool_call_id=uuid4().hex)
            ],
            usage=usage,
            model_name=self.model_name,
        )
