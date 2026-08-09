"""Tests for evaluator/dataset validation rules and JSON round-tripping."""

import pytest
from pydantic import ValidationError

from valcore.errors import ConfigError, ContractError
from valcore.models import (
    Dataset,
    EvaluatorVersion,
    ExperimentRun,
    FieldType,
    LabelSchema,
    OutputField,
    ScoreKind,
    check_dataset_compatibility,
    parse_output_fields,
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
        "must start with",
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
    """Build a dataset compatible with make_version(), applying any overrides."""
    base: dict[str, object] = {
        "name": "ds",
        "columns": ["question", "answer"],
        "label_schema": {"kind": "categorical", "labels": ["pass", "fail"]},
    }
    base.update(overrides)
    return Dataset(**base)


def test_compatible_dataset_passes() -> None:
    check_dataset_compatibility(make_version(), make_dataset())


def test_empty_label_schema_passes_categorical() -> None:
    # A dataset that declares no ground truth has nothing to disagree about, so it is
    # compatible with a categorical evaluator regardless of the evaluator's label space.
    dataset = make_dataset(label_schema={})
    check_dataset_compatibility(make_version(), dataset)


def test_empty_label_schema_passes_numeric() -> None:
    dataset = make_dataset(label_schema={})
    check_dataset_compatibility(make_numeric_version(), dataset)


def test_empty_label_schema_does_not_raise_validation_error() -> None:
    # Regression: an unlabeled uploaded dataset stores ``{}`` for its label schema, which
    # LabelSchema.model_validate cannot parse; the empty schema must short-circuit before
    # that call so no bare pydantic ValidationError escapes the compatibility check.
    dataset = make_dataset(label_schema={})
    try:
        check_dataset_compatibility(make_version(), dataset)
    except ValidationError as exc:  # pragma: no cover - fails loudly if the guard is missing
        pytest.fail(f"empty label_schema leaked a pydantic ValidationError: {exc}")


def test_dataset_missing_required_column() -> None:
    dataset = make_dataset(columns=["answer"])
    with pytest.raises(ContractError, match="missing required column") as exc:
        check_dataset_compatibility(make_version(), dataset)
    assert "question" in str(exc.value)


def test_dataset_kind_mismatch() -> None:
    dataset = make_dataset(label_schema={"kind": "numeric", "minimum": 0.0, "maximum": 1.0})
    with pytest.raises(ContractError, match="does not match evaluator score kind") as exc:
        check_dataset_compatibility(make_version(), dataset)
    assert "numeric" in str(exc.value)
    assert "categorical" in str(exc.value)


def test_dataset_label_sets_differ_names_offenders() -> None:
    dataset = make_dataset(label_schema={"kind": "categorical", "labels": ["pass", "maybe"]})
    with pytest.raises(ContractError) as exc:
        check_dataset_compatibility(make_version(), dataset)
    message = str(exc.value)
    assert "fail" in message
    assert "maybe" in message


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


def test_version_json_round_trip() -> None:
    version = make_version()
    dumped = version.model_dump()
    restored = EvaluatorVersion.model_validate(dumped)
    validate_version(restored)
    fields = parse_output_fields(restored)
    assert [f.name for f in fields] == ["verdict"]


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
