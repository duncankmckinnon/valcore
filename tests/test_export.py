"""Tests for rendering an EvaluatorVersion to a standalone Python script."""

import json

import pytest
from pydantic import ValidationError
from pydantic_evals import Case
from pydantic_evals import Dataset as EvalsDataset

from valcore.export import (
    render_dataset_module,
    render_judge_module,
    render_script,
    render_tool_sources,
)
from valcore.models import Dataset, DatasetRow, EvaluatorVersion, ScoreKind


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
def test_no_valcore_import(factory) -> None:
    """The exported script never imports from the valcore library."""
    src = render_script(factory())
    assert "import valcore" not in src
    assert "from valcore" not in src


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
    """Copied tool source is verbatim and runnable without any valcore dependency."""
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


# --- render_script regression --------------------------------------------------


def test_render_script_output_is_deterministic() -> None:
    """render_script is a pure function of its version: repeated calls are byte-identical.

    The code-renderer additions must not perturb the existing script output. The strongest
    guard remains the existing render_script tests above continuing to pass unmodified; this
    only pins that the render itself has no hidden nondeterminism (set ordering, etc.).
    """
    version = _tools_version()
    assert render_script(version) == render_script(version)


# --- render_tool_sources -------------------------------------------------------


def test_render_tool_sources_returns_imports_and_source() -> None:
    """A named tool yields its import block and its verbatim source for embedding."""
    imports, source = render_tool_sources(["regex_search"])
    assert "import re" in imports
    assert "def regex_search" in source


def test_render_tool_sources_empty_for_no_names() -> None:
    """An empty name list yields empty import and source blocks."""
    imports, source = render_tool_sources([])
    assert imports == ""
    assert source == ""


def test_render_tool_sources_source_execs_and_is_callable() -> None:
    """The rendered source is runnable on its own once its imports are provided."""
    imports, source = render_tool_sources(["regex_search"])
    ns = _exec_script(imports + "\n\n" + source)
    assert ns["regex_search"]("a1b2c3", r"\d") == ["1", "2", "3"]


# --- render_dataset_module -----------------------------------------------------


def _dataset() -> Dataset:
    """A small categorical dataset used by the dataset-module tests."""
    return Dataset(
        name="refusal-quality",
        columns=["question", "answer"],
        label_schema={"kind": "categorical", "labels": ["refusal", "answer"]},
    )


def _dataset_rows() -> list[DatasetRow]:
    """Three rows: two labelled, one unlabelled, to exercise expected_output omission."""
    return [
        DatasetRow(
            dataset_id="d1",
            idx=0,
            data={"question": "Q1", "answer": "A1"},
            label={"value": "refusal"},
        ),
        DatasetRow(
            dataset_id="d1",
            idx=1,
            data={"question": "Q2", "answer": "A2"},
            label={"value": "answer"},
        ),
        DatasetRow(
            dataset_id="d1",
            idx=2,
            data={"question": "Q3", "answer": "A3"},
            label=None,
        ),
    ]


def test_dataset_module_builds_pydantic_evals_dataset() -> None:
    """The module execs to a DATASET whose cases mirror the source rows."""
    ns = _exec_script(render_dataset_module(_dataset(), _dataset_rows()))
    ds = ns["DATASET"]
    assert isinstance(ds, EvalsDataset)
    assert ds.name == "refusal-quality"
    assert len(ds.cases) == 3
    assert ds.cases[0].inputs == {"question": "Q1", "answer": "A1"}
    assert ds.cases[0].expected_output == "refusal"
    assert ds.cases[1].expected_output == "answer"


def test_dataset_module_omits_expected_output_for_unlabelled_row() -> None:
    """A row with no label produces a Case with expected_output unset (None)."""
    ns = _exec_script(render_dataset_module(_dataset(), _dataset_rows()))
    ds = ns["DATASET"]
    assert ds.cases[2].expected_output is None
    # Only the two labelled rows emit an expected_output= argument in the source.
    src = render_dataset_module(_dataset(), _dataset_rows())
    assert src.count("expected_output=") == 2


def test_dataset_module_has_no_valcore_dependency() -> None:
    """The dataset module imports nothing from valcore and names the dataset in its docstring."""
    src = render_dataset_module(_dataset(), _dataset_rows())
    assert "import valcore" not in src
    assert "from valcore" not in src
    ns = _exec_script(src)
    assert "refusal-quality" in ns["__doc__"]


def test_dataset_module_uses_repr_so_values_round_trip() -> None:
    """Strings with quotes and nested dict inputs survive intact via repr()."""
    rows = [
        DatasetRow(
            dataset_id="d1",
            idx=0,
            data={"question": 'He said "hi"', "answer": {"nested": [1, 2]}},
            label={"value": "refusal"},
        ),
    ]
    ns = _exec_script(render_dataset_module(_dataset(), rows))
    case = ns["DATASET"].cases[0]
    assert case.inputs == {"question": 'He said "hi"', "answer": {"nested": [1, 2]}}


# --- render_judge_module -------------------------------------------------------


def _judge_package(model: str = "test") -> dict:
    """A bundled package matching the categorical version's rendered OutputModel.

    ``model`` defaults to the ``test`` known-model name so the judge runs under pydantic-ai's
    TestModel with no live gateway key.
    """
    return {
        "kind": "valcore/eval-package",
        "version": 1,
        "agent": {
            "model": model,
            "name": "v1",
            "instructions": "Judge the row.",
            "output_schema": {
                "type": "object",
                "properties": {
                    "verdict": {"type": "string", "enum": ["good", "bad"], "description": "x"}
                },
                "required": ["verdict"],
            },
        },
        "valcore": {
            "prompt_template": "Input: {text}",
            "required_columns": ["text"],
            "score_field": "verdict",
            "score_kind": "categorical",
            "score_labels": ["good", "bad"],
            "tools": [],
        },
    }


def test_judge_module_returns_categorical_label(tmp_path) -> None:
    """The exec'd ValcoreJudge scores a case to its categorical label under TestModel."""
    ns = _exec_script(render_judge_module(_categorical_version(), "package.json"))
    judge_cls = ns["ValcoreJudge"]

    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_judge_package()))

    dataset = EvalsDataset[dict, str, dict](
        name="d",
        cases=[Case(name="row-1", inputs={"text": "hello"}, expected_output="good")],
        evaluators=[judge_cls(package=str(pkg_path))],
    )

    async def task(inputs: dict) -> str:
        return "good"

    report = dataset.evaluate_sync(task)
    assert report.cases[0].labels["ValcoreJudge"].value == "good"


def test_judge_module_agent_output_is_real_model() -> None:
    """Building the agent as evaluate() does yields a real OutputModel, not a dict.

    Guards the output_type precedence fact: without an explicit output_type the agent would
    fall back to StructuredDict and getattr on the result would break at run time.
    """
    ns = _exec_script(render_judge_module(_categorical_version(), "package.json"))
    pkg = _judge_package()
    agent = ns["Agent"].from_spec(
        ns["AgentSpec"].from_dict(pkg["agent"]),
        output_type=ns["OutputModel"],
        defer_model_check=True,
    )
    result = agent.run_sync(pkg["valcore"]["prompt_template"].format(text="hi"))
    assert isinstance(result.output, ns["OutputModel"])
    assert getattr(result.output, pkg["valcore"]["score_field"]) == "good"


def test_judge_module_imports_neither_valcore_nor_yaml() -> None:
    """The companion module is self-contained: no valcore import and no yaml anywhere."""
    src = render_judge_module(_categorical_version(), "package.json")
    assert "import valcore" not in src
    assert "from valcore" not in src
    assert "yaml" not in src


def test_judge_module_omits_tools_and_capabilities_when_none() -> None:
    """A version with no tools and no capabilities emits neither keyword argument."""
    src = render_judge_module(_categorical_version(), "package.json")
    assert "tools=" not in src
    assert "custom_capability_types=" not in src


def test_judge_module_includes_tools_and_capabilities_when_present() -> None:
    """A version with tools and a capability inlines the tool and imports the capability."""
    version = _make_version(
        tools=["regex_search"],
        capabilities=[{"name": "CodeMode", "config": {"max_retries": 3}}],
    )
    src = render_judge_module(version, "package.json")
    assert "tools=[regex_search]" in src
    assert "custom_capability_types=[CodeMode]" in src
    assert "from pydantic_ai_harness import CodeMode" in src
    assert "def regex_search" in src
    # The rendered module is still valid, importable Python end-to-end.
    _exec_script(src)


def test_judge_module_reuses_output_model_renderer() -> None:
    """The judge's OutputModel matches render_script's — same private renderer, no drift."""
    version = _categorical_version()
    judge_src = render_judge_module(version, "package.json")
    script_ns = _exec_script(render_script(version))
    judge_ns = _exec_script(judge_src)
    assert set(judge_ns["OutputModel"].model_fields) == set(script_ns["OutputModel"].model_fields)
    assert "from typing import Literal" in judge_src
