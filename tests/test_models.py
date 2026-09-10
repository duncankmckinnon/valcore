"""Tests for evaluator/dataset validation rules and JSON round-tripping."""

import pytest
from pydantic import ValidationError

from valcore.errors import ConfigError, ContractError
from valcore.models import (
    Annotation,
    Dataset,
    EvaluatorVersion,
    ExperimentRun,
    FieldType,
    LabelSchema,
    LabelSet,
    OutputField,
    RunKind,
    ScoreKind,
    annotation_ground_truth,
    check_dataset_compatibility,
    find_matching_label_set,
    label_schema_from_label_set,
    label_set_fields_from_schema,
    parse_output_fields,
    validate_annotation,
    validate_label_set,
    validate_version,
)


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


def test_no_matching_label_set_fails_for_validation_kind() -> None:
    dataset = make_dataset()
    with pytest.raises(ContractError, match="No label set"):
        check_dataset_compatibility(make_version(), dataset, [], kind=RunKind.VALIDATION)


def test_dataset_missing_required_column() -> None:
    dataset = make_dataset(columns=["answer"])
    with pytest.raises(ContractError, match="missing required column") as exc:
        check_dataset_compatibility(make_version(), dataset, [_matching_label_set()])
    assert "question" in str(exc.value)


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
