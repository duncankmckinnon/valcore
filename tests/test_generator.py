"""Tests for the evaluator generator and refiner agents.

No network: agent behavior is driven by ``FunctionModel`` returning canned
structured outputs. These tests exercise the post-validation + single-retry
contract of ``generate_config`` / ``refine_config``.
"""

import copy

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from evalcore.errors import ConfigError
from evalcore.generator import (
    GeneratedConfig,
    RefinedConfig,
    generate_config,
    refine_config,
)

# A structurally- and semantically-valid GeneratedConfig payload: reasoning
# field precedes the enum score field, labels match the enum values.
VALID_CONFIG: dict = {
    "name": "Answer Correctness",
    "version_name": "v1",
    "instructions": "You are a judge. Explain your reasoning, then score.",
    "prompt_template": "Rate the answer to {question}.",
    "required_columns": ["question"],
    "output_fields": [
        {"name": "reasoning", "type": "str", "description": "Why this verdict."},
        {
            "name": "verdict",
            "type": "enum",
            "description": "The verdict.",
            "enum_values": ["pass", "fail"],
        },
    ],
    "score_field": "verdict",
    "score_kind": "categorical",
    "score_labels": ["pass", "fail"],
    "score_minimum": None,
    "score_maximum": None,
    "capabilities": [{"name": "CodeMode", "config": {}}],
    "tools": [],
    "rationale": "Categorical pass/fail with a reasoning-first schema.",
}

# Structurally a valid GeneratedConfig, but validate_version rejects it: the
# score_field names a field that is absent from output_fields.
INVALID_CONFIG: dict = {
    **copy.deepcopy(VALID_CONFIG),
    "output_fields": [
        {"name": "reasoning", "type": "str", "description": "Why this verdict."},
    ],
}


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


def generator_agent(payloads: list[dict]) -> tuple[Agent, CountingModel]:
    """Build a generator-typed agent backed by canned GeneratedConfig payloads."""
    counter = CountingModel(payloads)
    return Agent(counter.model(), output_type=GeneratedConfig), counter


def refiner_agent(payloads: list[dict]) -> tuple[Agent, CountingModel]:
    """Build a refiner-typed agent backed by canned RefinedConfig payloads."""
    counter = CountingModel(payloads)
    return Agent(counter.model(), output_type=RefinedConfig), counter


@pytest.mark.anyio
async def test_generate_valid_config_passes_through_unchanged() -> None:
    """A valid config is returned as-is after a single agent call."""
    agent, counter = generator_agent([VALID_CONFIG])

    result = await generate_config("Judge answer correctness.", agent=agent)

    assert isinstance(result, GeneratedConfig)
    assert counter.calls == 1
    assert result.name == "Answer Correctness"
    assert result.score_field == "verdict"
    assert [f.name for f in result.output_fields] == ["reasoning", "verdict"]
    assert result.score_labels == ["pass", "fail"]
    # No mutation of the config the model produced.
    assert result.model_dump(mode="json") == GeneratedConfig.model_validate(
        VALID_CONFIG
    ).model_dump(mode="json")


@pytest.mark.anyio
async def test_generate_invalid_config_triggers_exactly_one_retry() -> None:
    """An invalid config retries once and succeeds on the second call."""
    agent, counter = generator_agent([INVALID_CONFIG, VALID_CONFIG])

    result = await generate_config("Judge answer correctness.", agent=agent)

    assert counter.calls == 2  # one retry, not a loop
    assert result.score_field == "verdict"
    assert [f.name for f in result.output_fields] == ["reasoning", "verdict"]


@pytest.mark.anyio
async def test_generate_always_invalid_raises_after_single_retry() -> None:
    """A config that stays invalid raises ConfigError after exactly one retry."""
    agent, counter = generator_agent([INVALID_CONFIG])

    with pytest.raises(ConfigError):
        await generate_config("Judge answer correctness.", agent=agent)

    assert counter.calls == 2  # original + one retry, then it propagates


@pytest.mark.anyio
async def test_generate_columns_do_not_break_valid_config() -> None:
    """Passing available columns still yields a valid config in one call."""
    agent, counter = generator_agent([VALID_CONFIG])

    result = await generate_config(
        "Judge answer correctness.", columns=["question", "answer"], agent=agent
    )

    assert counter.calls == 1
    assert result.required_columns == ["question"]


@pytest.mark.anyio
async def test_refine_returns_full_config_with_changed_fields() -> None:
    """refine_config returns the complete config plus populated changed_fields."""
    refined_payload: dict = {
        "config": copy.deepcopy(VALID_CONFIG),
        "changed_fields": ["instructions", "prompt_template"],
        "summary": "Tightened the rubric and reworded the prompt.",
    }
    refined_payload["config"]["instructions"] = "Sharper rubric. Explain, then score."
    agent, counter = refiner_agent([refined_payload])

    current = GeneratedConfig.model_validate(VALID_CONFIG)
    result = await refine_config(current, "Tighten the rubric.", agent=agent)

    assert isinstance(result, RefinedConfig)
    assert counter.calls == 1
    assert result.changed_fields == ["instructions", "prompt_template"]
    assert result.summary
    # The full config is returned, not a patch.
    assert isinstance(result.config, GeneratedConfig)
    assert result.config.score_field == "verdict"
    assert result.config.instructions == "Sharper rubric. Explain, then score."


@pytest.mark.anyio
async def test_refine_invalid_config_triggers_exactly_one_retry() -> None:
    """A refined config that fails validation retries once, then succeeds."""
    bad = {
        "config": copy.deepcopy(INVALID_CONFIG),
        "changed_fields": ["output_fields"],
        "summary": "Broke the schema.",
    }
    good = {
        "config": copy.deepcopy(VALID_CONFIG),
        "changed_fields": ["output_fields"],
        "summary": "Fixed the schema.",
    }
    agent, counter = refiner_agent([bad, good])

    current = GeneratedConfig.model_validate(VALID_CONFIG)
    result = await refine_config(current, "Change the schema.", agent=agent)

    assert counter.calls == 2
    assert result.config.score_field == "verdict"
