"""Tests for rendering an EvaluatorVersion to a standalone Python script."""

import pytest
from pydantic import ValidationError

from evalcore.export import render_script
from evalcore.models import EvaluatorVersion, ScoreKind


def _make_version(**overrides: object) -> EvaluatorVersion:
    """Build an EvaluatorVersion with sensible defaults, overridable per test."""
    base: dict[str, object] = {
        "evaluator_id": "e1",
        "version_name": "v1",
        "model": "gateway/anthropic:claude-sonnet-5",
        "instructions": "Judge the row.",
        "prompt_template": "Input: {text}",
        "score_field": "verdict",
        "score_kind": ScoreKind.CATEGORICAL,
        "score_labels": ["good", "bad"],
        "required_columns": ["text"],
        "output_fields": [
            {
                "name": "verdict",
                "type": "enum",
                "description": "the call",
                "enum_values": ["good", "bad"],
            },
        ],
        "tools": [],
        "capabilities": [],
    }
    base.update(overrides)
    return EvaluatorVersion(**base)


def _exec_script(src: str) -> dict[str, object]:
    """Compile and exec a rendered script in a fresh namespace, returning the namespace."""
    code = compile(src, "<export>", "exec")
    ns: dict[str, object] = {}
    exec(code, ns)  # noqa: S102 - exercising the generated script is the point of this test
    return ns


def _categorical_version() -> EvaluatorVersion:
    return _make_version()


def _numeric_version() -> EvaluatorVersion:
    return _make_version(
        score_field="score",
        score_kind=ScoreKind.NUMERIC,
        score_labels=None,
        output_fields=[
            {
                "name": "score",
                "type": "float",
                "description": "a 0-1 quality score",
                "minimum": 0,
                "maximum": 1,
            },
            {
                "name": "rationale",
                "type": "str",
                "description": "why",
                "required": False,
            },
        ],
    )


def _tools_version() -> EvaluatorVersion:
    return _make_version(tools=["regex_search", "word_count"])


def _codemode_version() -> EvaluatorVersion:
    return _make_version(capabilities=[{"name": "CodeMode", "config": {"max_retries": 3}}])


TRICKY_INSTRUCTIONS = (
    "She said \"hello\" and 'bye'.\n"
    "Windows path: C:\\temp\\new\\file\n"
    "Braces: {not_a_field} and {{escaped}}\n"
    'Triple maybe: """ almost'
)


def _tricky_version() -> EvaluatorVersion:
    return _make_version(instructions=TRICKY_INSTRUCTIONS)


ALL_VERSIONS = {
    "categorical": _categorical_version,
    "numeric": _numeric_version,
    "with_tools": _tools_version,
    "codemode": _codemode_version,
    "tricky_instructions": _tricky_version,
}


@pytest.mark.parametrize("factory", ALL_VERSIONS.values(), ids=ALL_VERSIONS.keys())
def test_rendered_script_compiles(factory) -> None:
    """render_script output is syntactically valid Python for every kind of version."""
    src = render_script(factory())
    compile(src, "<export>", "exec")


@pytest.mark.parametrize("factory", ALL_VERSIONS.values(), ids=ALL_VERSIONS.keys())
def test_rendered_script_execs(factory) -> None:
    """The rendered script executes in a fresh namespace and defines the expected symbols."""
    ns = _exec_script(render_script(factory()))
    assert "OutputModel" in ns
    assert callable(ns["evaluate"])
    assert "PROMPT_TEMPLATE" in ns


@pytest.mark.parametrize("factory", ALL_VERSIONS.values(), ids=ALL_VERSIONS.keys())
def test_no_evalcore_import(factory) -> None:
    """The exported script never imports from the evalcore library."""
    src = render_script(factory())
    assert "import evalcore" not in src
    assert "from evalcore" not in src


def test_categorical_output_model_validation() -> None:
    """The enum score field renders as a Literal that accepts only its values."""
    ns = _exec_script(render_script(_categorical_version()))
    model = ns["OutputModel"]
    assert set(model.model_fields) == {"verdict"}

    assert model(verdict="good").verdict == "good"
    with pytest.raises(ValidationError):
        model(verdict="maybe")


def test_numeric_output_model_validation() -> None:
    """Numeric bounds render as ge/le and the optional field defaults to None."""
    ns = _exec_script(render_script(_numeric_version()))
    model = ns["OutputModel"]
    assert set(model.model_fields) == {"score", "rationale"}

    inst = model(score=0.5)
    assert inst.score == 0.5
    assert inst.rationale is None

    with pytest.raises(ValidationError):
        model(score=1.5)
    with pytest.raises(ValidationError):
        model(score=-0.1)


def test_required_vs_optional_fields() -> None:
    """A required field is mandatory; a non-required field carries a default of None."""
    ns = _exec_script(render_script(_numeric_version()))
    model = ns["OutputModel"]
    assert model.model_fields["score"].is_required()
    assert not model.model_fields["rationale"].is_required()
    with pytest.raises(ValidationError):
        model(rationale="missing score")


def test_selected_tool_source_present_unselected_absent() -> None:
    """Only the selected tools' source is copied into the script."""
    src = render_script(_tools_version())
    assert "def regex_search" in src
    assert "def word_count" in src
    # Unselected tools must not leak into the export.
    assert "def json_extract" not in src
    assert "def string_similarity" not in src
    assert "def numeric_compare" not in src


def test_tool_source_execs_and_is_callable() -> None:
    """Copied tool source is verbatim and runnable without any evalcore dependency."""
    ns = _exec_script(render_script(_tools_version()))
    assert ns["regex_search"]("a1b2c3", r"\d") == ["1", "2", "3"]
    assert ns["word_count"]("one two three") == 3


def test_no_tools_no_tool_block() -> None:
    """A version with no tools does not emit a tools=[...] argument or tool source."""
    src = render_script(_categorical_version())
    assert "tools=[" not in src
    assert "def regex_search" not in src


def test_codemode_capability_import_and_call() -> None:
    """A CodeMode capability yields its harness import and a constructor call in the agent."""
    src = render_script(_codemode_version())
    assert "from pydantic_ai_harness import CodeMode" in src
    assert "CodeMode(max_retries=3)" in src
    assert "capabilities=[" in src
    # The script must still be importable end-to-end.
    _exec_script(src)


def test_instructions_round_trip_byte_for_byte() -> None:
    """User instructions with quotes, newlines, backslashes, and braces survive intact."""
    version = _tricky_version()
    ns = _exec_script(render_script(version))
    assert ns["INSTRUCTIONS"] == version.instructions


def test_prompt_template_round_trip() -> None:
    """The prompt template survives the round trip and formats against a row."""
    version = _make_version(
        prompt_template='Row said "{text}"\nBackslash: \\ done',
        required_columns=["text"],
    )
    ns = _exec_script(render_script(version))
    assert ns["PROMPT_TEMPLATE"] == version.prompt_template
    assert ns["PROMPT_TEMPLATE"].format(text="hi") == 'Row said "hi"\nBackslash: \\ done'


def test_model_string_present() -> None:
    """The gateway model string is embedded as the MODEL constant."""
    ns = _exec_script(render_script(_make_version(model="gateway/openai:gpt-5")))
    assert ns["MODEL"] == "gateway/openai:gpt-5"


def test_literal_import_only_when_enum_present() -> None:
    """Literal is imported for enum output fields and omitted when there are none."""
    with_enum = render_script(_categorical_version())
    without_enum = render_script(_numeric_version())
    assert "from typing import Literal" in with_enum
    assert "Literal" not in without_enum


def test_docstring_names_version_and_gateway_key() -> None:
    """The module docstring names the version and states the gateway key requirement."""
    version = _make_version(version_name="my-eval")
    src = render_script(version)
    ns = _exec_script(src)
    doc = ns["__doc__"]
    assert "my-eval" in doc
    assert "PYDANTIC_AI_GATEWAY_API_KEY" in doc
