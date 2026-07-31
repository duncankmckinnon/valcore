"""Tests for building live evaluator agents from stored versions."""

import pytest
from pydantic import BaseModel, ValidationError
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from valcore.errors import ConfigError, ContractError
from valcore.factory import (
    build_agent,
    build_capabilities,
    build_output_model,
    extract_score,
    render_prompt,
)
from valcore.models import CapabilitySpec, EvaluatorVersion, ScoreKind


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make_version(**overrides: object) -> EvaluatorVersion:
    """Build a valid categorical EvaluatorVersion, applying any field overrides."""
    base: dict[str, object] = {
        "evaluator_id": "ev1",
        "version_name": "my eval",
        "model": "gateway/anthropic:claude-sonnet-5",
        "instructions": "You are an evaluator.",
        "prompt_template": "Rate the answer to {question}.",
        "required_columns": ["question"],
        "output_fields": [
            {
                "name": "verdict",
                "type": "enum",
                "description": "The verdict.",
                "enum_values": ["pass", "fail"],
            }
        ],
        "score_field": "verdict",
        "score_kind": ScoreKind.CATEGORICAL,
        "score_labels": ["pass", "fail"],
        "capabilities": [],
        "tools": [],
    }
    base.update(overrides)
    return EvaluatorVersion(**base)


# --- build_output_model -------------------------------------------------------


def test_output_model_field_names_order_types_and_descriptions() -> None:
    version = make_version(
        output_fields=[
            {"name": "reason", "type": "str", "description": "Why."},
            {"name": "count", "type": "int", "description": "How many."},
            {"name": "ratio", "type": "float", "description": "A ratio."},
            {"name": "ok", "type": "bool", "description": "Is it ok."},
        ],
        score_field="count",
        score_kind=ScoreKind.NUMERIC,
        score_labels=None,
    )
    model = build_output_model(version)
    fields = model.model_fields
    assert list(fields) == ["reason", "count", "ratio", "ok"]
    assert fields["reason"].annotation is str
    assert fields["count"].annotation is int
    assert fields["ratio"].annotation is float
    assert fields["ok"].annotation is bool
    assert fields["reason"].description == "Why."
    assert fields["count"].description == "How many."


def test_output_model_name_derived_from_version_name() -> None:
    assert build_output_model(make_version(version_name="my eval")).__name__ == "MyEvalOutput"


def test_output_model_name_falls_back_when_not_identifier() -> None:
    # A version name that titlecases to a non-identifier (leading digit).
    model = build_output_model(make_version(version_name="123 go"))
    assert model.__name__ == "EvaluatorOutput"


def test_enum_field_becomes_literal_rejecting_off_schema_value() -> None:
    model = build_output_model(make_version())
    assert model(verdict="pass").verdict == "pass"
    with pytest.raises(ValidationError):
        model(verdict="maybe")


def test_optional_field_defaults_to_none() -> None:
    version = make_version(
        output_fields=[
            {
                "name": "verdict",
                "type": "enum",
                "description": "V",
                "enum_values": ["pass", "fail"],
            },
            {"name": "note", "type": "str", "description": "N.", "required": False},
        ],
    )
    model = build_output_model(version)
    instance = model(verdict="pass")
    assert instance.note is None
    # Optional field still accepts a value.
    assert model(verdict="pass", note="hi").note == "hi"


def test_required_field_has_no_default() -> None:
    model = build_output_model(make_version())
    with pytest.raises(ValidationError):
        model()


def test_numeric_bounds_reject_out_of_range_values() -> None:
    version = make_version(
        output_fields=[
            {
                "name": "rating",
                "type": "int",
                "description": "1-5.",
                "minimum": 1,
                "maximum": 5,
            }
        ],
        score_field="rating",
        score_kind=ScoreKind.NUMERIC,
        score_labels=None,
    )
    model = build_output_model(version)
    assert model(rating=3).rating == 3
    with pytest.raises(ValidationError):
        model(rating=0)
    with pytest.raises(ValidationError):
        model(rating=6)


def test_float_lower_bound_only() -> None:
    version = make_version(
        output_fields=[
            {"name": "score", "type": "float", "description": "s", "minimum": 0.0},
        ],
        score_field="score",
        score_kind=ScoreKind.NUMERIC,
        score_labels=None,
    )
    model = build_output_model(version)
    assert model(score=1000.0).score == 1000.0
    with pytest.raises(ValidationError):
        model(score=-0.1)


# --- build_agent --------------------------------------------------------------


def test_build_agent_returns_agent_whose_output_type_round_trips() -> None:
    version = make_version()
    agent = build_agent(version)
    assert isinstance(agent, Agent)
    output_model = agent.output_type
    assert issubclass(output_model, BaseModel)
    parsed = output_model(verdict="fail")
    assert parsed.verdict == "fail"


def test_build_agent_validates_version_first() -> None:
    # An invalid model string must fail validation before any agent construction.
    with pytest.raises(ConfigError):
        build_agent(make_version(model="not-a-gateway-model"))


@pytest.mark.anyio
async def test_build_agent_run_with_testmodel_produces_extractable_score() -> None:
    version = make_version()
    agent = build_agent(version)
    with agent.override(model=TestModel()):
        result = await agent.run("evaluate this")
    assert version.score_field == "verdict"
    assert result.output.verdict in {"pass", "fail"}
    assert extract_score(version, result.output) == result.output.verdict


@pytest.mark.anyio
async def test_build_agent_numeric_score_round_trips() -> None:
    version = make_version(
        output_fields=[
            {
                "name": "rating",
                "type": "int",
                "description": "1-5.",
                "minimum": 1,
                "maximum": 5,
            }
        ],
        score_field="rating",
        score_kind=ScoreKind.NUMERIC,
        score_labels=None,
    )
    agent = build_agent(version)
    with agent.override(model=TestModel()):
        result = await agent.run("evaluate this")
    score = extract_score(version, result.output)
    assert isinstance(score, int)
    assert 1 <= score <= 5


# --- extract_score ------------------------------------------------------------


def test_extract_score_returns_plain_value() -> None:
    version = make_version(
        output_fields=[
            {"name": "rating", "type": "float", "description": "r"},
        ],
        score_field="rating",
        score_kind=ScoreKind.NUMERIC,
        score_labels=None,
    )

    class Out(BaseModel):
        rating: float

    assert extract_score(version, Out(rating=4.5)) == 4.5


def test_extract_score_coerces_enum_member_to_value() -> None:
    from enum import Enum

    class Verdict(str, Enum):
        PASS = "pass"
        FAIL = "fail"

    class Out(BaseModel):
        verdict: Verdict

    version = make_version()
    assert extract_score(version, Out(verdict=Verdict.PASS)) == "pass"


# --- render_prompt ------------------------------------------------------------


def test_render_prompt_substitutes_columns() -> None:
    version = make_version(prompt_template="Q: {question} A: {answer}")
    rendered = render_prompt(version, {"question": "2+2?", "answer": "4"})
    assert rendered == "Q: 2+2? A: 4"


def test_render_prompt_stringifies_non_string_values() -> None:
    version = make_version(prompt_template="n={n}")
    assert render_prompt(version, {"n": 42}) == "n=42"


def test_render_prompt_raises_contract_error_on_missing_column() -> None:
    version = make_version(prompt_template="Rate {question} and {answer}")
    with pytest.raises(ContractError) as exc:
        render_prompt(version, {"question": "hi"})
    assert "answer" in str(exc.value)


# --- build_capabilities -------------------------------------------------------


def test_build_capabilities_empty_returns_empty_list() -> None:
    assert build_capabilities([]) == []


def test_build_capabilities_builds_code_mode() -> None:
    caps = build_capabilities([CapabilitySpec(name="CodeMode", config={"max_retries": 2})])
    assert len(caps) == 1
    from pydantic_ai_harness.code_mode import CodeMode

    assert isinstance(caps[0], CodeMode)


def test_build_capabilities_unknown_name_raises_config_error_naming_capability() -> None:
    # Bypass CapabilitySpec name validation to exercise the factory's own guard.
    bogus = CapabilitySpec.model_construct(name="Bogus", config={})
    with pytest.raises(ConfigError) as exc:
        build_capabilities([bogus])
    assert "Bogus" in str(exc.value)


def test_build_capabilities_bad_kwargs_raises_config_error_naming_capability() -> None:
    spec = CapabilitySpec(name="CodeMode", config={"nonexistent_kwarg": 1})
    with pytest.raises(ConfigError) as exc:
        build_capabilities([spec])
    message = str(exc.value)
    assert "CodeMode" in message
    assert "nonexistent_kwarg" in message


def test_build_agent_with_code_mode_capability() -> None:
    version = make_version(capabilities=[{"name": "CodeMode", "config": {"max_retries": 2}}])
    agent = build_agent(version)
    assert isinstance(agent, Agent)
