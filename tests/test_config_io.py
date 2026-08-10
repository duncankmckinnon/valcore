"""Tests for JSON assembly, serialization, and format detection.

``config_io`` is the one place a valcore entity is turned into an eval-package JSON document
and back. It routes every foreign-model mapping through ``spec.py`` and never renders Python,
touches the store, or reads YAML. These tests pin the package format, the four-way format
detection in ``from_text``, and the round-trip guarantees, before any of that code exists.
"""

import json
import warnings
from dataclasses import dataclass

import pytest
from pydantic_ai.agent.spec import AgentSpec
from pydantic_evals import Dataset as EvalsDataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from valcore.config_io import EvalPackage, ValcoreMeta
from valcore.errors import ContractError
from valcore.models import Dataset as VDataset
from valcore.models import (
    DatasetRow,
    EvaluatorVersion,
    FieldType,
    LabelSource,
    OutputField,
    ScoreKind,
)
from valcore.spec import output_fields_to_schema

STEM = "refusal_quality"


# --- builders -----------------------------------------------------------------


def make_version(**overrides: object) -> EvaluatorVersion:
    """Build a valid categorical EvaluatorVersion, applying any field overrides."""
    base: dict[str, object] = {
        "evaluator_id": "ev1",
        "version_name": "refusal judge",
        "model": "openai:gpt-4o",
        "instructions": "You judge whether an answer is a refusal.",
        "prompt_template": "Q: {question}\nA: {answer}",
        "required_columns": ["question", "answer"],
        "output_fields": [
            {
                "name": "score",
                "type": "enum",
                "description": "the judgment",
                "enum_values": ["refusal", "partial", "answer"],
            }
        ],
        "score_field": "score",
        "score_kind": ScoreKind.CATEGORICAL,
        "score_labels": ["refusal", "partial", "answer"],
        "capabilities": [{"name": "CodeMode", "config": {}}],
        "tools": ["regex_search"],
    }
    base.update(overrides)
    return EvaluatorVersion(**base)


def make_rows() -> list[DatasetRow]:
    """Two rows: one fully labeled with a note, one with no label at all."""
    return [
        DatasetRow(
            dataset_id="d1",
            idx=0,
            data={"question": "Q1", "answer": "A1"},
            label={"value": "refusal"},
            label_source=LabelSource.MANUAL,
            note="a note",
        ),
        DatasetRow(
            dataset_id="d1",
            idx=1,
            data={"question": "Q2", "answer": "A2"},
        ),
    ]


def make_dataset() -> VDataset:
    """Build the valcore dataset the rows belong to."""
    return VDataset(name="refusal-quality", columns=["question", "answer"])


def full_package() -> EvalPackage:
    """A package carrying both halves: a version's agent+valcore and a dataset."""
    return EvalPackage.from_version(make_version()).merge(
        EvalPackage.from_dataset(make_dataset(), make_rows())
    )


# pydantic-evals resolves an evaluator by its class name and requires custom evaluators to be
# decorated with ``@dataclass``; this stand-in carries the name valcore emits into the registry.
@dataclass
class ValcoreJudge(Evaluator):
    """A minimal stand-in registered under the name pydantic-evals looks up."""

    package: str = ""

    def evaluate(self, ctx: EvaluatorContext) -> bool:
        return True


# --- constructors -------------------------------------------------------------


def test_from_version_builds_agent_and_valcore_only() -> None:
    pkg = EvalPackage.from_version(make_version())
    assert isinstance(pkg.spec, AgentSpec)
    assert isinstance(pkg.valcore, ValcoreMeta)
    assert pkg.dataset is None


def test_from_version_valcore_carries_the_untranslatable_fields() -> None:
    meta = EvalPackage.from_version(make_version()).valcore
    assert meta.prompt_template == "Q: {question}\nA: {answer}"
    assert meta.required_columns == ["question", "answer"]
    assert meta.score_field == "score"
    assert meta.score_kind is ScoreKind.CATEGORICAL
    assert meta.score_labels == ["refusal", "partial", "answer"]
    assert meta.tools == ["regex_search"]


def test_from_dataset_builds_dataset_only() -> None:
    pkg = EvalPackage.from_dataset(make_dataset(), make_rows())
    assert isinstance(pkg.dataset, EvalsDataset)
    assert pkg.spec is None
    assert pkg.valcore is None


# --- bundled serialization shape ----------------------------------------------


def test_to_text_bundled_returns_single_named_file() -> None:
    texts = full_package().to_text(STEM)
    assert set(texts) == {"refusal_quality.json"}


def test_to_text_content_is_indented_json_with_trailing_newline() -> None:
    content = full_package().to_text(STEM)["refusal_quality.json"]
    assert content.endswith("\n")
    # indent=2 means nested keys appear on their own indented lines.
    assert "\n  " in content
    json.loads(content)  # must be valid JSON


def test_bundled_document_carries_kind_version_and_three_sections() -> None:
    doc = json.loads(full_package().to_text(STEM)["refusal_quality.json"])
    assert doc["kind"] == "valcore/eval-package"
    assert doc["version"] == 1
    assert set(doc) >= {"kind", "version", "agent", "valcore", "dataset"}


def test_bundled_agent_section_is_reloadable_as_agentspec() -> None:
    doc = json.loads(full_package().to_text(STEM)["refusal_quality.json"])
    spec = AgentSpec.from_dict(doc["agent"])
    assert spec.model == "openai:gpt-4o"
    assert spec.output_schema["properties"]["score"]["enum"] == [
        "refusal",
        "partial",
        "answer",
    ]


def test_bundled_evaluators_reference_the_bundle_itself() -> None:
    doc = json.loads(full_package().to_text(STEM)["refusal_quality.json"])
    assert doc["dataset"]["evaluators"] == [{"ValcoreJudge": {"package": "refusal_quality.json"}}]


def test_dataset_only_package_emits_no_evaluators() -> None:
    pkg = EvalPackage.from_dataset(make_dataset(), make_rows())
    doc = json.loads(pkg.to_text(STEM)["refusal_quality.json"])
    assert doc["dataset"]["evaluators"] == []


# --- round-trip equality ------------------------------------------------------


def test_bundled_round_trip_reconstructs_version_fields() -> None:
    original = make_version()
    pkg = EvalPackage.from_version(original).merge(
        EvalPackage.from_dataset(make_dataset(), make_rows())
    )
    content = pkg.to_text(STEM)["refusal_quality.json"]

    fields = EvalPackage.from_text(content).to_version_fields()
    assert fields["model"] == original.model
    assert fields["version_name"] == original.version_name
    assert fields["instructions"] == original.instructions
    assert ScoreKind(fields["score_kind"]) is ScoreKind.CATEGORICAL
    assert fields["score_labels"] == ["refusal", "partial", "answer"]
    assert fields["tools"] == ["regex_search"]
    assert fields["capabilities"] == [{"name": "CodeMode", "config": {}}]

    # The reconstructed fields build a valid version whose enum output survives, and re-encode
    # to the byte-identical schema (schema_to_output_fields adds an explicit ``required``, so an
    # exact dict-equality with the leaner original would be wrong).
    rebuilt = EvaluatorVersion(evaluator_id="ev1", **fields)
    score = OutputField(**rebuilt.output_fields[0])
    assert score.name == "score"
    assert score.type is FieldType.ENUM
    assert score.enum_values == ["refusal", "partial", "answer"]
    assert output_fields_to_schema(rebuilt) == output_fields_to_schema(original)


def test_bundled_round_trip_preserves_output_field_order() -> None:
    original = make_version(
        output_fields=[
            {"name": "score", "type": "int", "description": "s"},
            {"name": "reason", "type": "str", "description": "why"},
            {"name": "ok", "type": "bool", "description": "b"},
        ],
        score_field="score",
        score_kind=ScoreKind.NUMERIC,
        score_labels=None,
    )
    content = EvalPackage.from_version(original).to_text(STEM)["refusal_quality.json"]
    fields = EvalPackage.from_text(content).to_version_fields()
    assert [f["name"] for f in fields["output_fields"]] == ["score", "reason", "ok"]


def test_bundled_round_trip_reconstructs_dataset_rows_and_labels() -> None:
    pkg = EvalPackage.from_version(make_version()).merge(
        EvalPackage.from_dataset(make_dataset(), make_rows())
    )
    content = pkg.to_text(STEM)["refusal_quality.json"]

    name, columns, label_schema, prepared = EvalPackage.from_text(content).to_dataset_fields()
    assert name == "refusal-quality"
    assert columns == ["question", "answer"]
    # The valcore block wins over inference, so the full label space survives.
    assert label_schema["kind"] == "categorical"
    assert label_schema["labels"] == ["refusal", "partial", "answer"]

    labeled, unlabeled = prepared
    assert labeled["data"] == {"question": "Q1", "answer": "A1"}
    assert labeled["label"] == {"value": "refusal"}
    assert labeled["label_source"] == "manual"
    assert labeled["note"] == "a note"
    assert "label" not in unlabeled


# --- split mode ---------------------------------------------------------------


def test_split_returns_two_named_files() -> None:
    texts = full_package().to_text(STEM, mode="split")
    assert set(texts) == {"refusal_quality.agent.json", "refusal_quality.dataset.json"}


def test_split_omits_the_missing_half() -> None:
    agent_only = EvalPackage.from_version(make_version()).to_text(STEM, mode="split")
    assert set(agent_only) == {"refusal_quality.agent.json"}

    dataset_only = EvalPackage.from_dataset(make_dataset(), make_rows()).to_text(STEM, mode="split")
    assert set(dataset_only) == {"refusal_quality.dataset.json"}


def test_split_agent_file_loads_with_bare_agentspec_ignoring_valcore() -> None:
    texts = full_package().to_text(STEM, mode="split")
    spec = AgentSpec.from_text(texts["refusal_quality.agent.json"], fmt="json")
    assert spec.model == "openai:gpt-4o"
    assert spec.output_schema["required"] == ["score"]
    # The valcore block sits beside the agent at top level and must be ignored, not rejected.
    assert "valcore" in json.loads(texts["refusal_quality.agent.json"])


def test_split_dataset_file_without_evaluator_loads_natively() -> None:
    # A dataset exported with no agent carries an empty evaluators list, so pydantic-evals
    # loads it with no custom_evaluator_types.
    texts = EvalPackage.from_dataset(make_dataset(), make_rows()).to_text(STEM, mode="split")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = EvalsDataset.from_text(texts["refusal_quality.dataset.json"], fmt="json")
    assert ds.name == "refusal-quality"
    assert [c.inputs for c in ds.cases] == [
        {"question": "Q1", "answer": "A1"},
        {"question": "Q2", "answer": "A2"},
    ]


def test_split_dataset_evaluators_reference_the_agent_sibling() -> None:
    texts = full_package().to_text(STEM, mode="split")
    doc = json.loads(texts["refusal_quality.dataset.json"])
    assert doc["evaluators"] == [{"ValcoreJudge": {"package": "refusal_quality.agent.json"}}]


def test_split_dataset_with_evaluator_needs_custom_type() -> None:
    # The conditional half of the guarantee: a dataset exported alongside an agent references
    # ValcoreJudge, so a bare load raises and only a supplied stub type succeeds.
    content = full_package().to_text(STEM, mode="split")["refusal_quality.dataset.json"]

    with (
        pytest.raises(BaseException),  # noqa: B017 - ExceptionGroup from the evaluator registry
        warnings.catch_warnings(),
    ):
        warnings.simplefilter("ignore")
        EvalsDataset.from_text(content, fmt="json")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = EvalsDataset.from_text(content, fmt="json", custom_evaluator_types=[ValcoreJudge])
    assert ds.name == "refusal-quality"


# --- format detection ---------------------------------------------------------


def test_from_text_detects_bundled() -> None:
    content = full_package().to_text(STEM)["refusal_quality.json"]
    pkg = EvalPackage.from_text(content)
    assert pkg.spec is not None
    assert pkg.valcore is not None
    assert pkg.dataset is not None


def test_from_text_detects_bare_agent_with_valcore_block() -> None:
    agent_file = EvalPackage.from_version(make_version()).to_text(STEM, mode="split")[
        "refusal_quality.agent.json"
    ]
    pkg = EvalPackage.from_text(agent_file)
    assert pkg.spec is not None
    assert pkg.spec.model == "openai:gpt-4o"
    assert pkg.valcore is not None
    assert pkg.dataset is None


def test_from_text_detects_bare_dataset() -> None:
    dataset_file = EvalPackage.from_dataset(make_dataset(), make_rows()).to_text(
        STEM, mode="split"
    )["refusal_quality.dataset.json"]
    pkg = EvalPackage.from_text(dataset_file)
    assert pkg.dataset is not None
    assert pkg.spec is None
    name, _columns, _schema, _prepared = pkg.to_dataset_fields()
    assert name == "refusal-quality"


def test_from_text_split_pair_recombines_via_merge() -> None:
    texts = full_package().to_text(STEM, mode="split")
    agent_pkg = EvalPackage.from_text(texts["refusal_quality.agent.json"])
    dataset_pkg = EvalPackage.from_text(texts["refusal_quality.dataset.json"])

    combined = agent_pkg.merge(dataset_pkg)
    assert combined.spec is not None
    assert combined.valcore is not None
    assert combined.dataset is not None
    # And the recombined package reconstructs the same version fields.
    assert combined.to_version_fields()["model"] == "openai:gpt-4o"


# --- bundled is not a bare dataset --------------------------------------------


def test_bundled_document_is_not_loadable_as_a_bare_dataset() -> None:
    content = full_package().to_text(STEM)["refusal_quality.json"]
    # pydantic-evals forbids unknown top-level keys, so the bundle's kind/agent/valcore keys
    # make it fail — the documented reason the bundled file needs config_io to read it.
    with pytest.raises(Exception) as exc, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        EvalsDataset.from_text(content, fmt="json")
    assert "extra_forbidden" in str(exc.value)


# --- rejections ---------------------------------------------------------------


def test_from_text_rejects_unknown_kind() -> None:
    content = json.dumps({"kind": "valcore/something-else", "version": 1})
    with pytest.raises(ContractError):
        EvalPackage.from_text(content)


def test_from_text_rejects_unsupported_version() -> None:
    content = json.dumps({"kind": "valcore/eval-package", "version": 2, "agent": {}})
    with pytest.raises(ContractError) as exc:
        EvalPackage.from_text(content)
    assert "2" in str(exc.value)


def test_from_text_rejects_a_root_that_is_neither_object_nor_case_array() -> None:
    # An array root is now read as a bare case list (Logfire's export shape), so the rejection
    # here is about the *contents* not being cases -- and about a scalar root having no shape at
    # all. Both must surface as ContractError rather than a raw pydantic or JSON error.
    with pytest.raises(ContractError):
        EvalPackage.from_text(json.dumps([1, 2, 3]))
    with pytest.raises(ContractError):
        EvalPackage.from_text(json.dumps("just a string"))


def test_from_text_rejects_unparseable_json() -> None:
    with pytest.raises(ContractError):
        EvalPackage.from_text("{not valid json")


def test_from_text_rejects_unrecognized_document() -> None:
    with pytest.raises(ContractError):
        EvalPackage.from_text(json.dumps({"greeting": "hello"}))


def test_from_text_rejects_foreign_evaluator_name() -> None:
    doc = {
        "kind": "valcore/eval-package",
        "version": 1,
        "dataset": {
            "name": "d",
            "cases": [],
            "evaluators": [{"SomeOtherJudge": {}}],
        },
    }
    with pytest.raises(ContractError) as exc:
        EvalPackage.from_text(json.dumps(doc))
    assert "SomeOtherJudge" in str(exc.value)


def test_merge_rejects_two_agents() -> None:
    with pytest.raises(ContractError):
        EvalPackage.from_version(make_version()).merge(EvalPackage.from_version(make_version()))


def test_merge_rejects_two_datasets() -> None:
    left = EvalPackage.from_dataset(make_dataset(), make_rows())
    right = EvalPackage.from_dataset(make_dataset(), make_rows())
    with pytest.raises(ContractError) as exc:
        left.merge(right)
    assert "dataset" in str(exc.value)


def test_merge_rejects_two_valcore_blocks() -> None:
    # Two agent halves also each carry a valcore block; the merge must name a conflicting half.
    with pytest.raises(ContractError) as exc:
        EvalPackage.from_version(make_version()).merge(EvalPackage.from_version(make_version()))
    assert "agent" in str(exc.value) or "valcore" in str(exc.value)


def test_from_text_rejects_foreign_evaluator_given_as_bare_string() -> None:
    # An evaluators entry may be a bare string name rather than a single-key object; the guard
    # must reject a non-ValcoreJudge string just as it rejects the object form.
    doc = {
        "kind": "valcore/eval-package",
        "version": 1,
        "dataset": {"name": "d", "cases": [], "evaluators": ["SomeOtherJudge"]},
    }
    with pytest.raises(ContractError) as exc:
        EvalPackage.from_text(json.dumps(doc))
    assert "SomeOtherJudge" in str(exc.value)


# --- missing-section guards ---------------------------------------------------


def test_to_version_fields_without_agent_raises() -> None:
    pkg = EvalPackage.from_dataset(make_dataset(), make_rows())
    with pytest.raises(ContractError):
        pkg.to_version_fields()


def test_to_dataset_fields_without_dataset_raises() -> None:
    pkg = EvalPackage.from_version(make_version())
    with pytest.raises(ContractError):
        pkg.to_dataset_fields()


# --- ExceptionGroup unwrapping ------------------------------------------------


def test_exception_group_is_flattened_to_contract_error_naming_evaluator() -> None:
    # A bare pydantic-evals dataset naming an evaluator the registry cannot resolve makes
    # Dataset.from_dict raise an ExceptionGroup; from_text must surface a single flat
    # ContractError that names the offending evaluator, not the group's opaque summary.
    doc = {
        "name": "d",
        "cases": [{"name": "r", "inputs": {"q": "x"}}],
        "evaluators": [{"MysteryJudge": {}}],
    }
    with pytest.raises(ContractError) as exc:
        EvalPackage.from_text(json.dumps(doc))
    assert not isinstance(exc.value, BaseExceptionGroup)
    assert "MysteryJudge" in str(exc.value)
