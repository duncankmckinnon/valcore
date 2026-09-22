"""Tests for evaluator/dataset validation rules and JSON round-tripping."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from valcore.errors import ConfigError, ContractError
from valcore.models import (
    Agent,
    AgentResponse,
    AgentVersion,
    Annotation,
    Dataset,
    DatasetDerivation,
    DatasetRow,
    DerivationRole,
    DerivationState,
    DerivationStatus,
    EvaluatorVersion,
    ExperimentRun,
    FieldType,
    LabelSchema,
    LabelSet,
    OutputField,
    RunDerivation,
    RunKind,
    ScoreKind,
    annotation_ground_truth,
    check_agent_dataset_compatibility,
    check_dataset_compatibility,
    find_matching_label_set,
    label_schema_from_label_set,
    label_set_fields_from_schema,
    parse_output_fields,
    validate_agent_version,
    validate_annotation,
    validate_label_set,
    validate_version,
)
from valcore.store import create_engine, init_db


def make_version(**overrides: object) -> EvaluatorVersion:
    """Build a valid categorical EvaluatorVersion, applying any field overrides."""
    base: dict[str, object] = {
        "evaluator_id": "ev1",
        "version_name": "v1",
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


def make_numeric_version(**overrides: object) -> EvaluatorVersion:
    """Build a valid numeric EvaluatorVersion, applying any field overrides."""
    base: dict[str, object] = {
        "evaluator_id": "ev1",
        "version_name": "v1",
        "model": "gateway/anthropic:claude-sonnet-5",
        "instructions": "You are an evaluator.",
        "prompt_template": "Rate {question}.",
        "required_columns": ["question"],
        "output_fields": [
            {
                "name": "rating",
                "type": "float",
                "description": "A rating.",
                "minimum": 0.0,
                "maximum": 1.0,
            }
        ],
        "score_field": "rating",
        "score_kind": ScoreKind.NUMERIC,
        "score_labels": None,
    }
    base.update(overrides)
    return EvaluatorVersion(**base)


def test_valid_categorical_version_passes() -> None:
    validate_version(make_version())


def test_valid_numeric_version_passes() -> None:
    validate_version(make_numeric_version())


VALIDATE_VERSION_REJECTIONS = [
    pytest.param(
        {"model": "openai:gpt-5"},
        "start with one of",
        id="invalid-model-string",
    ),
    pytest.param(
        {"output_fields": []},
        "at least one output field",
        id="empty-output-fields",
    ),
    pytest.param(
        {"score_field": "missing"},
        "does not name an output field",
        id="score-field-not-a-field",
    ),
    pytest.param(
        {
            "output_fields": [{"name": "verdict", "type": "str", "description": "d"}],
            "score_labels": None,
        },
        "must be an enum",
        id="categorical-score-field-not-enum",
    ),
    pytest.param(
        {"score_labels": ["pass", "fail", "unsure"]},
        "must exactly match",
        id="categorical-labels-mismatch",
    ),
    pytest.param(
        {"required_columns": []},
        "at least one required column",
        id="empty-required-columns",
    ),
    pytest.param(
        {"prompt_template": "Rate {question} and {missing}."},
        "not in required_columns",
        id="template-references-unknown-column",
    ),
    pytest.param(
        {"capabilities": [{"name": "Telepathy", "config": {}}]},
        "Unknown capability",
        id="unknown-capability",
    ),
    pytest.param(
        {"model": "local/claude", "tools": ["word_count"]},
        "does not support tool calls",
        id="local-cli-model-with-tools",
    ),
    pytest.param(
        {
            "output_fields": [
                {
                    "name": "verdict",
                    "type": "enum",
                    "description": "d",
                    "enum_values": ["pass", "fail"],
                },
                {
                    "name": "verdict",
                    "type": "str",
                    "description": "dup",
                },
            ],
        },
        "must be unique",
        id="colliding-field-names",
    ),
]


@pytest.mark.parametrize("overrides,message", VALIDATE_VERSION_REJECTIONS)
def test_validate_version_rejections(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        validate_version(make_version(**overrides))


def test_valid_version_with_known_capability_passes() -> None:
    validate_version(make_version(capabilities=[{"name": "CodeMode", "config": {}}]))


def test_categorical_labels_out_of_order_rejected() -> None:
    with pytest.raises(ConfigError, match="must exactly match"):
        validate_version(make_version(score_labels=["fail", "pass"]))


def test_numeric_score_field_wrong_type_rejected() -> None:
    version = make_numeric_version(
        output_fields=[
            {
                "name": "rating",
                "type": "enum",
                "description": "d",
                "enum_values": ["a", "b"],
            }
        ],
    )
    with pytest.raises(ConfigError, match="must be an int or float"):
        validate_version(version)


def make_dataset(**overrides: object) -> Dataset:
    base: dict[str, object] = {"name": "ds", "columns": ["question", "answer"]}
    base.update(overrides)
    return Dataset(**base)


def _matching_label_set(**overrides: object) -> LabelSet:
    base: dict[str, object] = {
        "dataset_id": "ds-1",
        "name": "quality",
        "description": "",
        "kind": ScoreKind.CATEGORICAL,
        "labels": [{"name": "pass", "description": "d"}, {"name": "fail", "description": "d"}],
    }
    base.update(overrides)
    return LabelSet(**base)


def test_compatible_dataset_returns_matching_label_set() -> None:
    dataset = make_dataset()
    label_set = _matching_label_set()
    result = check_dataset_compatibility(make_version(), dataset, [label_set])
    assert result is label_set


def test_no_label_sets_passes_for_eval_kind() -> None:
    dataset = make_dataset()
    result = check_dataset_compatibility(make_version(), dataset, [], kind=RunKind.EVAL)
    assert result is None


def test_empty_label_sets_passes_for_validation_kind() -> None:
    # Mirrors the old empty label_schema: no label space declared is legal and returns
    # None without raising; the real "nothing to validate" rejection happens one level
    # up, in runner.py/experiment.py, once zero rows turn out to have ground truth.
    dataset = make_dataset()
    result = check_dataset_compatibility(make_version(), dataset, [], kind=RunKind.VALIDATION)
    assert result is None


def test_nonempty_but_no_matching_label_set_fails_for_validation_kind() -> None:
    dataset = make_dataset()
    wrong_kind = _matching_label_set(kind=ScoreKind.NUMERIC, labels=None, minimum=0.0, maximum=1.0)
    with pytest.raises(ContractError, match="No label set"):
        check_dataset_compatibility(make_version(), dataset, [wrong_kind], kind=RunKind.VALIDATION)


def test_dataset_missing_required_column() -> None:
    dataset = make_dataset(columns=["answer"])
    with pytest.raises(ContractError, match="missing required column") as exc:
        check_dataset_compatibility(make_version(), dataset, [_matching_label_set()])
    assert "question" in str(exc.value)


def test_derivation_supplies_evaluator_required_column() -> None:
    """A derived view extends the dataset contract with response columns."""
    dataset = make_dataset(columns=["question"])
    version = make_version(required_columns=["question", "draft"])
    derivation = DatasetDerivation(
        dataset_id=dataset.id,
        agent_version_id="av-1",
        response_columns=["draft"],
    )

    check_dataset_compatibility(version, dataset, [], kind=RunKind.EVAL, derivation=derivation)

    with pytest.raises(ContractError, match="draft"):
        check_dataset_compatibility(version, dataset, [], kind=RunKind.EVAL)


def test_derivation_compatibility_error_names_effective_contract() -> None:
    dataset = make_dataset(columns=["question"])
    derivation = DatasetDerivation(
        dataset_id=dataset.id,
        agent_version_id="av-1",
        response_columns=["draft"],
    )

    with pytest.raises(ContractError, match="effective contract") as exc:
        check_dataset_compatibility(
            make_version(required_columns=["question", "final"]),
            dataset,
            [],
            kind=RunKind.EVAL,
            derivation=derivation,
        )

    assert "final" in str(exc.value)
    assert "draft" in str(exc.value)


def test_validation_cannot_target_derivation_without_derivation_label_set() -> None:
    dataset = make_dataset()
    derivation = DatasetDerivation(
        dataset_id=dataset.id,
        agent_version_id="av-1",
        response_columns=["draft"],
    )

    with pytest.raises(ContractError, match="label sets"):
        check_dataset_compatibility(make_version(), dataset, [], derivation=derivation)


def test_dataset_kind_mismatch_no_match() -> None:
    dataset = make_dataset()
    numeric_label_set = _matching_label_set(
        kind=ScoreKind.NUMERIC, labels=None, minimum=0.0, maximum=1.0
    )
    with pytest.raises(ContractError, match="No label set"):
        check_dataset_compatibility(make_version(), dataset, [numeric_label_set])


def test_dataset_label_names_differ_no_match() -> None:
    dataset = make_dataset()
    label_set = _matching_label_set(
        labels=[{"name": "pass", "description": "d"}, {"name": "maybe", "description": "d"}]
    )
    with pytest.raises(ContractError, match="No label set"):
        check_dataset_compatibility(make_version(), dataset, [label_set])


def test_output_field_valid_enum() -> None:
    OutputField(name="verdict", type=FieldType.ENUM, description="d", enum_values=["a", "b"])


OUTPUT_FIELD_REJECTIONS = [
    pytest.param(
        {"name": "verdict", "type": FieldType.ENUM, "description": "d"},
        id="enum-without-values",
    ),
    pytest.param(
        {"name": "x", "type": FieldType.STR, "description": "d", "enum_values": ["a"]},
        id="non-enum-with-values",
    ),
    pytest.param(
        {"name": "x", "type": FieldType.STR, "description": "d", "minimum": 0.0},
        id="bounds-on-non-numeric",
    ),
    pytest.param(
        {"name": "x", "type": FieldType.INT, "description": "d", "minimum": 5.0, "maximum": 1.0},
        id="min-greater-than-max",
    ),
    pytest.param(
        {"name": "not an identifier", "type": FieldType.STR, "description": "d"},
        id="invalid-identifier",
    ),
    pytest.param(
        {"name": "class", "type": FieldType.STR, "description": "d"},
        id="python-keyword-name",
    ),
]


@pytest.mark.parametrize("kwargs", OUTPUT_FIELD_REJECTIONS)
def test_output_field_rejections(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        OutputField(**kwargs)


def test_label_schema_valid_categorical() -> None:
    LabelSchema(kind=ScoreKind.CATEGORICAL, labels=["a", "b"])


def test_label_schema_valid_numeric() -> None:
    LabelSchema(kind=ScoreKind.NUMERIC, minimum=0.0, maximum=1.0)


LABEL_SCHEMA_REJECTIONS = [
    pytest.param({"kind": ScoreKind.CATEGORICAL}, id="categorical-without-labels"),
    pytest.param(
        {"kind": ScoreKind.CATEGORICAL, "labels": ["a"], "minimum": 0.0},
        id="categorical-with-bounds",
    ),
    pytest.param({"kind": ScoreKind.NUMERIC, "labels": ["a"]}, id="numeric-with-labels"),
    pytest.param(
        {"kind": ScoreKind.NUMERIC, "minimum": 5.0, "maximum": 1.0},
        id="numeric-min-greater-than-max",
    ),
]


@pytest.mark.parametrize("kwargs", LABEL_SCHEMA_REJECTIONS)
def test_label_schema_rejections(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        LabelSchema(**kwargs)


# -- LabelSet ---------------------------------------------------------------


def make_label_set(**overrides: object) -> LabelSet:
    base: dict[str, object] = {
        "dataset_id": "ds-1",
        "name": "quality",
        "description": "",
        "kind": ScoreKind.CATEGORICAL,
        "labels": [{"name": "good", "description": "meets the bar"}],
    }
    base.update(overrides)
    return LabelSet(**base)


def test_label_set_valid_categorical() -> None:
    label_set = make_label_set()
    validate_label_set(label_set)


def test_label_set_valid_numeric() -> None:
    label_set = make_label_set(kind=ScoreKind.NUMERIC, labels=None, minimum=0.0, maximum=1.0)
    validate_label_set(label_set)


LABEL_SET_REJECTIONS = [
    pytest.param({"labels": None}, id="categorical-without-labels"),
    pytest.param({"labels": []}, id="categorical-with-empty-labels"),
    pytest.param({"minimum": 0.0}, id="categorical-with-bounds"),
    pytest.param(
        {"labels": [{"name": "good", "description": "a"}, {"name": "good", "description": "b"}]},
        id="categorical-with-duplicate-label-names",
    ),
    pytest.param(
        {"labels": [{"name": "good"}]},
        id="categorical-with-malformed-label",
    ),
]


@pytest.mark.parametrize("overrides", LABEL_SET_REJECTIONS)
def test_label_set_categorical_rejections(overrides: dict[str, object]) -> None:
    label_set = make_label_set(**overrides)
    with pytest.raises(ContractError):
        validate_label_set(label_set)


NUMERIC_LABEL_SET_REJECTIONS = [
    pytest.param({"labels": [{"name": "good", "description": "d"}]}, id="numeric-with-labels"),
    pytest.param({"minimum": 5.0, "maximum": 1.0}, id="numeric-min-greater-than-max"),
]


@pytest.mark.parametrize("overrides", NUMERIC_LABEL_SET_REJECTIONS)
def test_label_set_numeric_rejections(overrides: dict[str, object]) -> None:
    base = {"kind": ScoreKind.NUMERIC, "labels": None, "minimum": 0.0, "maximum": 1.0}
    base.update(overrides)
    label_set = make_label_set(**base)
    with pytest.raises(ContractError):
        validate_label_set(label_set)


def test_version_json_round_trip() -> None:
    version = make_version()
    dumped = version.model_dump()
    restored = EvaluatorVersion.model_validate(dumped)
    validate_version(restored)
    fields = parse_output_fields(restored)
    assert [f.name for f in fields] == ["verdict"]


# -- Annotation ---------------------------------------------------------------


def test_validate_annotation_accepts_known_categorical_labels() -> None:
    label_set = make_label_set(
        labels=[{"name": "good", "description": "d"}, {"name": "bad", "description": "d"}]
    )
    validate_annotation(label_set, labels=["good"], value=None)
    validate_annotation(label_set, labels=["good", "bad"], value=None)


def test_validate_annotation_rejects_unknown_categorical_label() -> None:
    label_set = make_label_set(labels=[{"name": "good", "description": "d"}])
    with pytest.raises(ContractError, match="Unknown label"):
        validate_annotation(label_set, labels=["great"], value=None)


def test_validate_annotation_rejects_value_on_categorical_set() -> None:
    label_set = make_label_set()
    with pytest.raises(ContractError, match="must not set a numeric value"):
        validate_annotation(label_set, labels=[], value=1.0)


def test_validate_annotation_accepts_in_range_numeric_value() -> None:
    label_set = make_label_set(kind=ScoreKind.NUMERIC, labels=None, minimum=0.0, maximum=1.0)
    validate_annotation(label_set, labels=None, value=0.5)


def test_validate_annotation_rejects_out_of_range_numeric_value() -> None:
    label_set = make_label_set(kind=ScoreKind.NUMERIC, labels=None, minimum=0.0, maximum=1.0)
    with pytest.raises(ContractError, match="outside this label set's range"):
        validate_annotation(label_set, labels=None, value=5.0)


def test_validate_annotation_rejects_labels_on_numeric_set() -> None:
    label_set = make_label_set(kind=ScoreKind.NUMERIC, labels=None, minimum=0.0, maximum=1.0)
    with pytest.raises(ContractError, match="must not set labels"):
        validate_annotation(label_set, labels=["good"], value=0.5)


# -- find_matching_label_set --------------------------------------------------


def _label_set(id_suffix: str, **overrides: object) -> LabelSet:
    label_set = make_label_set(**overrides)
    label_set.id = f"ls-{id_suffix}"
    return label_set


def test_find_matching_label_set_exact_categorical_match() -> None:
    target = _label_set(
        "match", labels=[{"name": "good", "description": "d"}, {"name": "bad", "description": "d"}]
    )
    other = _label_set("other", labels=[{"name": "x", "description": "d"}])
    found = find_matching_label_set(
        [other, target], score_kind=ScoreKind.CATEGORICAL, score_labels=["good", "bad"]
    )
    assert found is target


def test_find_matching_label_set_label_names_must_match_exactly() -> None:
    label_set = _label_set("a", labels=[{"name": "good", "description": "d"}])
    found = find_matching_label_set(
        [label_set], score_kind=ScoreKind.CATEGORICAL, score_labels=["good", "bad"]
    )
    assert found is None


def test_find_matching_label_set_kind_mismatch_returns_none() -> None:
    label_set = _label_set("a", kind=ScoreKind.NUMERIC, labels=None, minimum=0.0, maximum=1.0)
    found = find_matching_label_set(
        [label_set], score_kind=ScoreKind.CATEGORICAL, score_labels=["good"]
    )
    assert found is None


def test_find_matching_label_set_exact_numeric_match() -> None:
    target = _label_set("a", kind=ScoreKind.NUMERIC, labels=None, minimum=0.0, maximum=1.0)
    found = find_matching_label_set(
        [target],
        score_kind=ScoreKind.NUMERIC,
        score_labels=None,
        score_minimum=0.0,
        score_maximum=1.0,
    )
    assert found is target


def test_find_matching_label_set_numeric_bounds_must_match_exactly() -> None:
    label_set = _label_set("a", kind=ScoreKind.NUMERIC, labels=None, minimum=0.0, maximum=1.0)
    found = find_matching_label_set(
        [label_set],
        score_kind=ScoreKind.NUMERIC,
        score_labels=None,
        score_minimum=0.0,
        score_maximum=5.0,
    )
    assert found is None


def test_find_matching_label_set_multiple_matches_returns_most_recent() -> None:
    first = _label_set("first", labels=[{"name": "good", "description": "d"}])
    second = _label_set("second", labels=[{"name": "good", "description": "d"}])
    found = find_matching_label_set(
        [first, second], score_kind=ScoreKind.CATEGORICAL, score_labels=["good"]
    )
    assert found is second


def test_find_matching_label_set_no_label_sets_returns_none() -> None:
    found = find_matching_label_set([], score_kind=ScoreKind.CATEGORICAL, score_labels=["good"])
    assert found is None


# -- Conversion helpers -------------------------------------------------------


def test_label_set_fields_from_schema_categorical() -> None:
    schema = LabelSchema(kind=ScoreKind.CATEGORICAL, labels=["good", "bad"])
    fields = label_set_fields_from_schema(schema)
    assert fields == {
        "kind": ScoreKind.CATEGORICAL,
        "labels": [{"name": "good", "description": ""}, {"name": "bad", "description": ""}],
        "minimum": None,
        "maximum": None,
    }


def test_label_set_fields_from_schema_numeric() -> None:
    schema = LabelSchema(kind=ScoreKind.NUMERIC, minimum=0.0, maximum=1.0)
    fields = label_set_fields_from_schema(schema)
    assert fields == {"kind": ScoreKind.NUMERIC, "labels": None, "minimum": 0.0, "maximum": 1.0}


def test_label_schema_from_label_set_categorical() -> None:
    label_set = make_label_set(
        labels=[{"name": "good", "description": "d"}, {"name": "bad", "description": "d"}]
    )
    schema = label_schema_from_label_set(label_set)
    assert schema == LabelSchema(kind=ScoreKind.CATEGORICAL, labels=["good", "bad"])


def test_label_schema_from_label_set_numeric() -> None:
    label_set = make_label_set(kind=ScoreKind.NUMERIC, labels=None, minimum=0.0, maximum=1.0)
    schema = label_schema_from_label_set(label_set)
    assert schema == LabelSchema(kind=ScoreKind.NUMERIC, minimum=0.0, maximum=1.0)


def test_annotation_ground_truth_categorical_single_label() -> None:
    label_set = make_label_set(labels=[{"name": "good", "description": "d"}])
    annotation = Annotation(label_set_id=label_set.id, dataset_row_id="row-1", labels=["good"])
    assert annotation_ground_truth(label_set, annotation) == "good"


def test_annotation_ground_truth_categorical_zero_or_multiple_labels_is_none() -> None:
    label_set = make_label_set(
        labels=[{"name": "good", "description": "d"}, {"name": "bad", "description": "d"}]
    )
    empty = Annotation(label_set_id=label_set.id, dataset_row_id="row-1", labels=[])
    multi = Annotation(label_set_id=label_set.id, dataset_row_id="row-2", labels=["good", "bad"])
    assert annotation_ground_truth(label_set, empty) is None
    assert annotation_ground_truth(label_set, multi) is None


def test_annotation_ground_truth_numeric() -> None:
    label_set = make_label_set(kind=ScoreKind.NUMERIC, labels=None, minimum=0.0, maximum=1.0)
    annotation = Annotation(label_set_id=label_set.id, dataset_row_id="row-1", labels=[], value=0.5)
    assert annotation_ground_truth(label_set, annotation) == 0.5


def test_annotation_ground_truth_none_annotation_is_none() -> None:
    label_set = make_label_set()
    assert annotation_ground_truth(label_set, None) is None


# -- ExperimentRun ------------------------------------------------------------


def test_experiment_run_defaults() -> None:
    """A run with no ExperimentRun row is a runner run; case_count defaults to 0."""
    experiment = ExperimentRun(run_id="run1", experiment_name="exp1")

    assert experiment.run_id == "run1"
    assert experiment.experiment_name == "exp1"
    assert experiment.case_count == 0
    assert experiment.id
    assert experiment.created_at is not None


def test_experiment_run_ids_are_unique_per_instance() -> None:
    first = ExperimentRun(run_id="run1", experiment_name="exp1")
    second = ExperimentRun(run_id="run1", experiment_name="exp1")

    assert first.id != second.id


def test_dataset_has_no_label_schema_field() -> None:
    assert "label_schema" not in Dataset.model_fields


def test_dataset_row_has_no_legacy_label_fields() -> None:
    legacy = {"label", "suggested_label", "label_reasoning", "label_source", "note"}
    assert legacy.isdisjoint(DatasetRow.model_fields)


# -- AgentVersion ---------------------------------------------------------------


def make_agent_version(**overrides: object) -> AgentVersion:
    """Build a valid AgentVersion with a minimal spec, applying any field overrides."""
    base: dict[str, object] = {
        "agent_id": "ag1",
        "version_name": "v1",
        "model": "gateway/anthropic:claude-sonnet-5",
        "spec": {"instructions": "hi"},
        "prompt_template": "Rate {question}.",
        "required_columns": ["question"],
        "deps_mapping": {},
    }
    base.update(overrides)
    return AgentVersion(**base)


def test_valid_agent_version_passes() -> None:
    validate_agent_version(make_agent_version())


def test_agent_version_bad_model_raises() -> None:
    with pytest.raises(ConfigError, match="start with one of"):
        validate_agent_version(make_agent_version(model="openai:gpt-5"))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("model", None, "model must be a string"),
        ("spec", [], "spec must be an object"),
        ("prompt_template", None, "prompt_template must be a string"),
        ("required_columns", {"question"}, "required_columns must be a list of strings"),
        ("required_columns", [1], "required_columns must be a list of strings"),
        ("deps_mapping", [], "deps_mapping must be an object of string values"),
        ("deps_mapping", {"name": 1}, "deps_mapping must be an object of string values"),
    ],
)
def test_agent_version_binding_fields_require_json_compatible_types(
    field: str, value: object, message: str
) -> None:
    """Table models must reject malformed JSON binding values before persistence."""
    with pytest.raises(ConfigError, match=message):
        validate_agent_version(make_agent_version(**{field: value}))


def test_agent_version_malformed_spec_raises() -> None:
    with pytest.raises(ConfigError, match="Invalid agent spec"):
        validate_agent_version(make_agent_version(spec={"retries": "not-an-int"}))


def test_agent_version_unknown_capability_raises() -> None:
    with pytest.raises(ConfigError, match="Telepathy") as exc:
        validate_agent_version(
            make_agent_version(spec={"instructions": "hi", "capabilities": ["Telepathy"]})
        )
    # The error should list the allowed capability names so a caller can self-correct.
    assert "WebSearch" in str(exc.value)
    assert "FileSystem" in str(exc.value)


def test_agent_version_subagents_capability_raises_specific_message() -> None:
    with pytest.raises(ConfigError, match="serialize") as exc:
        validate_agent_version(
            make_agent_version(spec={"instructions": "hi", "capabilities": ["SubAgents"]})
        )
    assert "SubAgents" in str(exc.value)


def test_agent_version_pydantic_ai_builtin_capability_passes() -> None:
    validate_agent_version(
        make_agent_version(spec={"instructions": "hi", "capabilities": ["WebSearch"]})
    )


def test_agent_version_harness_capability_passes() -> None:
    validate_agent_version(
        make_agent_version(spec={"instructions": "hi", "capabilities": ["FileSystem"]})
    )


def test_agent_version_configured_mcp_capability_passes() -> None:
    validate_agent_version(
        make_agent_version(spec={"instructions": "hi", "capabilities": [{"MCP": {"servers": []}}]})
    )


def test_agent_version_configured_subagents_capability_raises_specific_message() -> None:
    with pytest.raises(ConfigError, match="SubAgents.*serialize"):
        validate_agent_version(
            make_agent_version(spec={"instructions": "hi", "capabilities": [{"SubAgents": {}}]})
        )


def test_agent_version_prompt_template_unknown_column_raises() -> None:
    with pytest.raises(ConfigError):
        validate_agent_version(
            make_agent_version(
                prompt_template="Rate {question} and {missing}.",
                required_columns=["question"],
            )
        )


def test_agent_version_deps_mapping_unknown_field_raises() -> None:
    spec = {
        "instructions": "hi {name}",
        "deps_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        },
    }
    with pytest.raises(ConfigError):
        validate_agent_version(make_agent_version(spec=spec, deps_mapping={"bogus": "question"}))


def test_agent_version_missing_required_deps_mapping_raises() -> None:
    spec = {
        "instructions": "hi {name}",
        "deps_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    }
    with pytest.raises(ConfigError):
        validate_agent_version(make_agent_version(spec=spec, deps_mapping={}))


def test_agent_version_deps_mapping_satisfying_required_property_passes() -> None:
    spec = {
        "instructions": "hi {name}",
        "deps_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    }
    validate_agent_version(make_agent_version(spec=spec, deps_mapping={"name": "question"}))


def test_agent_dataset_compatibility_accepts_satisfied_columns() -> None:
    check_agent_dataset_compatibility(make_agent_version(), make_dataset(columns=["question"]))


def test_agent_dataset_compatibility_rejects_missing_column() -> None:
    with pytest.raises(ContractError, match="question"):
        check_agent_dataset_compatibility(make_agent_version(), make_dataset(columns=["answer"]))


# -- Agent / AgentVersion / DatasetDerivation defaults -------------------------


def test_agent_defaults() -> None:
    agent = Agent(name="my-agent")
    assert agent.id
    assert agent.created_at is not None
    assert agent.description == ""
    assert agent.active_version_id is None


def test_agent_version_defaults() -> None:
    version = make_agent_version()
    assert version.id
    assert version.created_at is not None
    assert version.notes == ""
    assert version.frozen is False


def test_agent_version_has_no_evaluator_scoring_fields() -> None:
    evaluator_only = {
        "output_fields",
        "score_field",
        "score_kind",
        "score_labels",
        "score_minimum",
        "score_maximum",
        "capabilities",
        "tools",
    }
    assert evaluator_only.isdisjoint(AgentVersion.model_fields)


def test_dataset_derivation_defaults() -> None:
    derivation = DatasetDerivation(dataset_id="ds-1", agent_version_id="av-1")
    assert derivation.id
    assert derivation.created_at is not None
    assert derivation.ordinal == 0
    assert derivation.response_columns == []


# -- Derivation status and run links ------------------------------------------


def test_run_kind_derive_round_trips_through_its_string_value() -> None:
    assert RunKind.DERIVE.value == "derive"
    assert RunKind("derive") is RunKind.DERIVE


def test_derivation_status_unique_constraint(tmp_path: Path) -> None:
    """A derivation has one lifecycle state, regardless of how it is read or filled."""
    engine = create_engine(tmp_path / "models.db")
    init_db(engine)
    try:
        with Session(engine) as session:
            session.add(DerivationStatus(derivation_id="deriv-1", state=DerivationState.STAGED))
            session.commit()

            session.add(DerivationStatus(derivation_id="deriv-1", state=DerivationState.SAVED))
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        engine.dispose()


def test_derivation_status_and_run_derivation_persist_and_rehydrate(tmp_path: Path) -> None:
    engine = create_engine(tmp_path / "models.db")
    init_db(engine)
    try:
        with Session(engine) as session:
            status = DerivationStatus(derivation_id="deriv-1", state=DerivationState.STAGED)
            fills = RunDerivation(
                run_id="derive-run",
                derivation_id=status.derivation_id,
                role=DerivationRole.FILLS,
            )
            reads = RunDerivation(
                run_id="eval-run",
                derivation_id=status.derivation_id,
                role=DerivationRole.READS,
            )
            session.add(status)
            session.add(fills)
            session.add(reads)
            session.commit()

            session.expire_all()
            restored_status = session.get(DerivationStatus, status.id)
            restored_links = session.exec(select(RunDerivation)).all()

            assert restored_status is not None
            assert restored_status.derivation_id == "deriv-1"
            assert restored_status.state is DerivationState.STAGED
            assert {(link.run_id, link.role) for link in restored_links} == {
                ("derive-run", DerivationRole.FILLS),
                ("eval-run", DerivationRole.READS),
            }
    finally:
        engine.dispose()


# -- AgentResponse --------------------------------------------------------------


def test_agent_response_unique_constraint(tmp_path: Path) -> None:
    """(derivation_id, dataset_row_id) must be unique across AgentResponse rows."""
    engine = create_engine(tmp_path / "models.db")
    init_db(engine)
    try:
        with Session(engine) as session:
            first = AgentResponse(
                derivation_id="deriv-1", dataset_row_id="row-1", data={"response": "a"}
            )
            session.add(first)
            session.commit()

            second = AgentResponse(
                derivation_id="deriv-1", dataset_row_id="row-1", data={"response": "b"}
            )
            session.add(second)
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        engine.dispose()


def test_agent_response_allows_same_row_across_different_derivations(tmp_path: Path) -> None:
    engine = create_engine(tmp_path / "models.db")
    init_db(engine)
    try:
        with Session(engine) as session:
            session.add(AgentResponse(derivation_id="deriv-1", dataset_row_id="row-1", data={}))
            session.commit()
            session.add(AgentResponse(derivation_id="deriv-2", dataset_row_id="row-1", data={}))
            session.commit()
            assert len(session.exec(select(AgentResponse)).all()) == 2
    finally:
        engine.dispose()


def test_agent_response_and_derivation_json_and_telemetry_round_trip(tmp_path: Path) -> None:
    engine = create_engine(tmp_path / "models.db")
    init_db(engine)
    try:
        with Session(engine) as session:
            derivation = DatasetDerivation(
                dataset_id="ds-1",
                agent_version_id="av-1",
                ordinal=2,
                response_columns=["answer", "confidence"],
            )
            response = AgentResponse(
                derivation_id=derivation.id,
                dataset_row_id="row-1",
                data={"answer": "hello", "confidence": 0.9},
                latency_ms=125,
                usage={"input_tokens": 7, "output_tokens": 2},
                error="provider warning",
            )
            session.add(derivation)
            session.add(response)
            session.commit()

            session.expire_all()
            restored_derivation = session.get(DatasetDerivation, derivation.id)
            restored_response = session.get(AgentResponse, response.id)

            assert restored_derivation is not None
            assert restored_derivation.response_columns == ["answer", "confidence"]
            assert restored_response is not None
            assert restored_response.data == {"answer": "hello", "confidence": 0.9}
            assert restored_response.latency_ms == 125
            assert restored_response.usage == {"input_tokens": 7, "output_tokens": 2}
            assert restored_response.error == "provider warning"
    finally:
        engine.dispose()
