"""Tests for the evaluator generator and refiner agents.

No network: agent behavior is driven by ``FunctionModel`` returning canned
structured outputs. These tests exercise the post-validation + single-retry
contract of ``generate_config`` / ``refine_config``.
"""

import copy

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from valcore.errors import ConfigError
from valcore.generator import (
    GeneratedConfig,
    RefinedConfig,
    generate_config,
    refine_config,
)
from valcore.models import LabelSchema, ScoreKind

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


def _latest_user_prompt(messages: list[ModelMessage]) -> str:
    """Return the most recent user-prompt text carried in ``messages``."""
    for message in reversed(messages):
        for part in getattr(message, "parts", []):
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                return part.content
    return ""


class CapturingModel:
    """A FunctionModel wrapper that records the prompt text of each invocation.

    The generator options are expressed entirely as prompt text, so the tests
    assert on what the agent actually receives rather than on any post-processed
    output. ``prompts`` holds the user-prompt string seen on every call, letting a
    retry's amended prompt be inspected too.
    """

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


def capturing_generator_agent(payloads: list[dict]) -> tuple[Agent, CapturingModel]:
    """Build a generator-typed agent that records the prompt it is handed."""
    capture = CapturingModel(payloads)
    return Agent(capture.model(), output_type=GeneratedConfig), capture


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
async def test_column_notes_roles_appear_in_prompt() -> None:
    """Each column's role from ``column_notes`` is stated in the prompt verbatim."""
    agent, capture = capturing_generator_agent([VALID_CONFIG])

    await generate_config(
        "Judge answer correctness.",
        column_notes={
            "question": "the user's request; context only, not the thing being judged",
            "answer": "the text being assessed",
            "locale": "ignore for scoring",
        },
        agent=agent,
    )

    prompt = capture.prompts[0]
    assert "question" in prompt
    assert "the user's request; context only, not the thing being judged" in prompt
    assert "answer" in prompt
    assert "the text being assessed" in prompt
    assert "locale" in prompt
    assert "ignore for scoring" in prompt


@pytest.mark.anyio
async def test_column_without_note_still_listed_in_prompt() -> None:
    """A column in ``columns`` but missing from ``column_notes`` still appears."""
    agent, capture = capturing_generator_agent([VALID_CONFIG])

    await generate_config(
        "Judge answer correctness.",
        columns=["question", "answer", "locale"],
        column_notes={"question": "the text being assessed"},
        agent=agent,
    )

    prompt = capture.prompts[0]
    # The annotated column carries its role, and the bare columns are not dropped.
    assert "the text being assessed" in prompt
    assert "answer" in prompt
    assert "locale" in prompt


@pytest.mark.anyio
async def test_dataset_columns_are_the_only_allowed_required_columns_and_placeholders() -> None:
    """The per-call prompt prohibits invented and explicitly irrelevant columns."""
    agent, capture = capturing_generator_agent([VALID_CONFIG])

    await generate_config(
        "Judge answer correctness.",
        columns=["question", "answer", "locale"],
        column_notes={"locale": "irrelevant to scoring; ignore it"},
        agent=agent,
    )

    prompt = capture.prompts[0]
    assert "`required_columns` may contain only the dataset columns listed above" in prompt
    assert "irrelevant or ignored must appear in neither" in prompt
    assert "`required_columns` nor `prompt_template`" in prompt
    assert "Every `{column}` placeholder" in prompt
    assert "do not invent columns or placeholders" in prompt


@pytest.mark.anyio
async def test_no_column_notes_keeps_current_prompt_form() -> None:
    """With ``column_notes=None`` the prompt keeps its existing columns form."""
    agent, capture = capturing_generator_agent([VALID_CONFIG])

    await generate_config(
        "Judge answer correctness.",
        columns=["question", "answer"],
        agent=agent,
    )

    prompt = capture.prompts[0]
    # The pre-existing behavior: a plain "Available dataset columns" listing with
    # no per-column role annotations introduced by the new option.
    assert "Available dataset columns" in prompt
    assert "question" in prompt
    assert "answer" in prompt
    assert "how each factors into the assessment" not in prompt


@pytest.mark.anyio
async def test_categorical_label_schema_states_labels_as_constraint() -> None:
    """A categorical ``label_schema`` puts its labels in the prompt as a constraint."""
    agent, capture = capturing_generator_agent([VALID_CONFIG])

    await generate_config(
        "Judge answer correctness.",
        label_schema=LabelSchema(kind=ScoreKind.CATEGORICAL, labels=["good", "bad"]),
        agent=agent,
    )

    prompt = capture.prompts[0]
    assert "categorical" in prompt.lower()
    assert "good" in prompt
    assert "bad" in prompt


@pytest.mark.anyio
async def test_numeric_label_schema_states_bounds_as_constraint() -> None:
    """A numeric ``label_schema`` with bounds states those bounds in the prompt."""
    agent, capture = capturing_generator_agent([VALID_CONFIG])

    await generate_config(
        "Judge answer correctness.",
        label_schema=LabelSchema(kind=ScoreKind.NUMERIC, minimum=1.0, maximum=5.0),
        agent=agent,
    )

    prompt = capture.prompts[0]
    assert "numeric" in prompt.lower()
    assert "1" in prompt
    assert "5" in prompt


@pytest.mark.anyio
async def test_no_label_schema_states_no_score_space_constraint() -> None:
    """With ``label_schema=None`` the prompt imposes no score-space constraint."""
    agent, capture = capturing_generator_agent([VALID_CONFIG])

    await generate_config("Judge answer correctness.", agent=agent)

    prompt = capture.prompts[0]
    # No categorical/numeric score-space constraint is stated when unconstrained.
    assert "score_labels must" not in prompt
    assert "score_kind must" not in prompt


@pytest.mark.anyio
async def test_generator_options_survive_the_single_retry() -> None:
    """The options are steering text only: an invalid first config still retries once."""
    agent, capture = capturing_generator_agent([INVALID_CONFIG, VALID_CONFIG])

    result = await generate_config(
        "Judge answer correctness.",
        column_notes={"question": "the text being assessed"},
        label_schema=LabelSchema(kind=ScoreKind.CATEGORICAL, labels=["pass", "fail"]),
        agent=agent,
    )

    assert capture.calls == 2  # one retry, not a loop
    assert result.score_field == "verdict"
    # The steering context is preserved into the retry prompt, not discarded.
    assert "the text being assessed" in capture.prompts[1]
    assert "pass" in capture.prompts[1]


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
