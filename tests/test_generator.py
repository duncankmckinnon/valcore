"""Tests for the generic ``ConfigGenerator`` base class.

No network: agent behavior is driven by ``FunctionModel`` returning canned
structured outputs. These tests exercise the run, validate, and single-retry
contract that ``ConfigGenerator`` provides to every subclass, using a toy
subclass rather than the real evaluator or agent generators.
"""

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from valcore.errors import ConfigError, ContractError
from valcore.generator import ConfigGenerator, GeneratedConfigBase, Refinement
from valcore.models import CapabilitySpec


@pytest.fixture
def anyio_backend() -> str:
    """Run anyio-marked async tests against asyncio only."""
    return "asyncio"


VALID_PAYLOAD: dict = {
    "version_name": "v1",
    "instructions": "Be fun.",
    "prompt_template": "Describe {toy}.",
    "required_columns": ["toy"],
    "capabilities": [],
    "rationale": "ok",
}


class _ToyConfig(GeneratedConfigBase):
    rationale: str

    def to_version(self, model: str) -> "_ToyConfig":
        return self


class _ToyRefinement(Refinement[_ToyConfig]):
    pass


class _ToyGenerator(ConfigGenerator[_ToyConfig]):
    subject = "toy"
    request_label = "Toy request"
    config_type = _ToyConfig
    refinement_type = _ToyRefinement

    def role_instructions(self) -> str:
        return "You design toys."

    def allowed_capabilities(self) -> list[str]:
        return ["Planning"]

    def check_version(self, version: _ToyConfig) -> None:
        if version.version_name == "bad":
            raise ConfigError("version_name must not be 'bad'.")


class CountingModel:
    """A FunctionModel wrapper that records how many times it was invoked."""

    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads
        self.calls = 0

    def model(self) -> FunctionModel:
        """Return a FunctionModel that emits the next canned payload per call."""

        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            payload = self.payloads[min(self.calls, len(self.payloads) - 1)]
            self.calls += 1
            tool = info.output_tools[0]
            return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=payload)])

        return FunctionModel(fn)


def _latest_user_prompt(messages: list[ModelMessage]) -> str:
    """Return the most recent user-prompt text carried in ``messages``."""
    for message in reversed(messages):
        for part in getattr(message, "parts", []):
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                return part.content
    return ""


class CapturingModel:
    """A FunctionModel wrapper that records the prompt text of each invocation."""

    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads
        self.prompts: list[str] = []

    @property
    def calls(self) -> int:
        """Number of times the backing model was invoked."""
        return len(self.prompts)

    def model(self) -> FunctionModel:
        """Return a FunctionModel that captures each prompt then emits a payload."""

        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            payload = self.payloads[min(len(self.prompts), len(self.payloads) - 1)]
            self.prompts.append(_latest_user_prompt(messages))
            tool = info.output_tools[0]
            return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=payload)])

        return FunctionModel(fn)


@pytest.mark.anyio
async def test_generate_returns_valid_config_with_expected_prompt() -> None:
    """generate() returns the valid payload as a config and sends the expected prompt."""
    capture = CapturingModel([VALID_PAYLOAD])
    agent = Agent(capture.model(), output_type=_ToyConfig)
    generator = _ToyGenerator(model="local/claude")

    result = await generator.generate("make a kite", agent=agent)

    assert isinstance(result, _ToyConfig)
    assert result.version_name == "v1"
    assert capture.calls == 1
    assert capture.prompts[0] == "Toy request:\nmake a kite"


@pytest.mark.anyio
async def test_generate_invalid_config_retries_once_then_succeeds() -> None:
    """An invalid first config retries once, with the retry prompt naming the failure."""
    bad_payload = {**VALID_PAYLOAD, "version_name": "bad"}
    capture = CapturingModel([bad_payload, VALID_PAYLOAD])
    agent = Agent(capture.model(), output_type=_ToyConfig)
    generator = _ToyGenerator(model="local/claude")

    result = await generator.generate("make a kite", agent=agent)

    assert result.version_name == "v1"
    assert capture.calls == 2
    assert "The previous configuration was invalid" in capture.prompts[1]


def test_validate_config_rejects_unknown_capability() -> None:
    """validate_config raises ConfigError naming an unknown capability."""
    generator = _ToyGenerator(model="local/claude")
    config = _ToyConfig(
        **{**VALID_PAYLOAD, "capabilities": [CapabilitySpec(name="Shell", config={})]}
    )

    with pytest.raises(ConfigError, match="Shell"):
        generator.validate_config(config)


@pytest.mark.anyio
async def test_generate_exhausted_retries_raises_contract_error() -> None:
    """A model that never returns a usable output surfaces as ContractError."""
    counter = CountingModel([{}])
    agent = Agent(counter.model(), output_type=_ToyConfig)
    generator = _ToyGenerator(model="local/claude")

    with pytest.raises(ContractError, match="describe the toy request more concretely"):
        await generator.generate("make a kite", agent=agent)


@pytest.mark.anyio
async def test_refine_returns_refinement_with_valid_config() -> None:
    """refine() returns a Refinement wrapping the valid config from the agent."""
    refine_payload = {
        "config": VALID_PAYLOAD,
        "changed_fields": ["instructions"],
        "summary": "s",
    }
    capture = CapturingModel([refine_payload])
    agent = Agent(capture.model(), output_type=_ToyRefinement)
    generator = _ToyGenerator(model="local/claude")
    current = _ToyConfig.model_validate(VALID_PAYLOAD)

    result = await generator.refine(current, "change it", agent=agent)

    assert isinstance(result, _ToyRefinement)
    assert isinstance(result.config, _ToyConfig)
    assert result.config.version_name == "v1"
    assert result.changed_fields == ["instructions"]


def test_build_agents_have_expected_names_and_local_model() -> None:
    """The built generator and refiner agents are named per subject and use the local CLI model."""
    from valcore.local_cli.bridge_model import CliBridgeModel

    generator = _ToyGenerator(model="local/claude")

    generator_agent = generator.build_generator_agent()
    refiner_agent = generator.build_refiner_agent()

    assert generator_agent.name == "toy_generator"
    assert refiner_agent.name == "toy_refiner"
    assert isinstance(generator_agent.model, CliBridgeModel)
    assert isinstance(refiner_agent.model, CliBridgeModel)
