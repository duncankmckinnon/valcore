"""Tests for CliBridgeModel, driven by a fake CliAdapter (no real CLI needed).

FakeCliAdapter stands in for a real adapter: instead of shelling out to claude/codex/
cursor, it runs a trivial `python -c` command that prints a canned string, so these tests
exercise the real CliBridgeModel machinery (instruction-building, subprocess handling,
JSON/code-fence parsing, error paths) without any real CLI installed.
"""

import sys

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.usage import RequestUsage

from valcore.local_cli.bridge_model import CliBridgeModel


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class JudgeOutput(BaseModel):
    verdict: str


class FakeCliAdapter:
    """Runs `python -c` to print a canned string instead of a real CLI binary."""

    cli_name = "fake"

    def __init__(self, *, stdout_text: str, usage: RequestUsage) -> None:
        self._stdout_text = stdout_text
        self._usage = usage
        self.last_invocation: dict[str, str] | None = None

    def build_invocation(self, *, prompt, instructions, model_name, run_dir) -> list[str]:
        self.last_invocation = {"prompt": prompt, "instructions": instructions, "model_name": model_name}
        return [sys.executable, "-c", f"import sys; sys.stdout.write({self._stdout_text!r})"]

    def parse_output(self, stdout: str) -> tuple[str, RequestUsage]:
        return stdout, self._usage


@pytest.mark.anyio
async def test_cli_bridge_model_parses_output_into_structured_result_and_usage() -> None:
    adapter = FakeCliAdapter(
        stdout_text='{"verdict": "pass"}', usage=RequestUsage(input_tokens=5, output_tokens=7)
    )
    model = CliBridgeModel(adapter, model_name="fake-model")
    agent = Agent(model, output_type=JudgeOutput, instructions="judge it")

    result = await agent.run("rate this")

    assert result.output == JudgeOutput(verdict="pass")
    assert result.usage.input_tokens == 5
    assert result.usage.output_tokens == 7
    assert adapter.last_invocation is not None
    assert adapter.last_invocation["prompt"] == "rate this"
    assert "judge it" in adapter.last_invocation["instructions"]
    assert "JSON Schema" in adapter.last_invocation["instructions"]


@pytest.mark.anyio
async def test_cli_bridge_model_tolerates_a_markdown_code_fence() -> None:
    fenced = '```json\n{"verdict": "pass"}\n```'
    adapter = FakeCliAdapter(stdout_text=fenced, usage=RequestUsage())
    model = CliBridgeModel(adapter, model_name="fake-model")
    agent = Agent(model, output_type=JudgeOutput, instructions="judge it")

    result = await agent.run("rate this")

    assert result.output == JudgeOutput(verdict="pass")


@pytest.mark.anyio
async def test_cli_bridge_model_raises_on_nonzero_exit() -> None:
    class FailingAdapter:
        cli_name = "failing"

        def build_invocation(self, **_kwargs: object) -> list[str]:
            return [sys.executable, "-c", "import sys; sys.exit(1)"]

        def parse_output(self, stdout: str) -> tuple[str, RequestUsage]:
            raise AssertionError("should not be called")

    model = CliBridgeModel(FailingAdapter(), model_name="fake-model")
    agent = Agent(model, output_type=JudgeOutput, instructions="x")

    with pytest.raises(RuntimeError, match="exited with code 1"):
        await agent.run("rate this")


@pytest.mark.anyio
async def test_cli_bridge_model_raises_when_output_is_not_json() -> None:
    adapter = FakeCliAdapter(stdout_text="not json at all", usage=RequestUsage())
    model = CliBridgeModel(adapter, model_name="fake-model")
    agent = Agent(model, output_type=JudgeOutput, instructions="x")

    with pytest.raises(RuntimeError, match="did not return a JSON object"):
        await agent.run("rate this")
