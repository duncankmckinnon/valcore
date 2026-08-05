"""Tests for pure shape derivation between evaluators and datasets."""

import pytest
from valcore.seeding import dataset_shape_from_version, evaluator_seed_from_dataset

from valcore.errors import ContractError
from valcore.models import Dataset, EvaluatorVersion, LabelSchema, ScoreKind


def make_version(**overrides: object) -> EvaluatorVersion:
    """Build a valid categorical EvaluatorVersion, applying any field overrides."""
    base: dict[str, object] = {
        "evaluator_id": "ev1",
        "version_name": "my eval",
        "model": "gateway/anthropic:claude-sonnet-5",
        "instructions": "You are an evaluator.",
        "prompt_template": "Rate the answer to {question}.",
        "required_columns": ["question", "answer"],
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


def numeric_version(**overrides: object) -> EvaluatorVersion:
    """Build a valid numeric EvaluatorVersion, applying any field overrides."""
    base: dict[str, object] = {
        "score_field": "rating",
        "score_kind": ScoreKind.NUMERIC,
        "score_labels": None,
        "output_fields": [
            {"name": "rating", "type": "int", "description": "1-5.", "minimum": 1, "maximum": 5}
        ],
    }
    base.update(overrides)
    return make_version(**base)


def make_dataset(**overrides: object) -> Dataset:
    """Build a Dataset, applying any field overrides."""
    base: dict[str, object] = {
        "name": "my dataset",
        "columns": ["question", "answer"],
        "label_schema": {"kind": "categorical", "labels": ["pass", "fail"]},
    }
    base.update(overrides)
    return Dataset(**base)


# --- dataset_shape_from_version: columns --------------------------------------


class TestDatasetShapeColumns:
    """Column ordering and validation when deriving a dataset shape."""

    def test_required_columns_first_then_extras_in_order(self) -> None:
        version = make_version(required_columns=["question", "answer"])
        columns, _ = dataset_shape_from_version(version, ["context", "source"])
        assert columns == ["question", "answer", "context", "source"]

    def test_required_column_order_is_preserved(self) -> None:
        version = make_version(required_columns=["z", "a", "m"])
        columns, _ = dataset_shape_from_version(version, [])
        assert columns == ["z", "a", "m"]

    def test_empty_extras_yields_exactly_required_columns(self) -> None:
        version = make_version(required_columns=["question", "answer"])
        columns, _ = dataset_shape_from_version(version, [])
        assert columns == ["question", "answer"]

    def test_extra_duplicating_required_raises_naming_column(self) -> None:
        version = make_version(required_columns=["question", "answer"])
        with pytest.raises(ContractError) as exc:
            dataset_shape_from_version(version, ["answer"])
        assert "answer" in str(exc.value)

    def test_extra_duplicating_required_names_only_the_offender(self) -> None:
        version = make_version(required_columns=["question", "answer"])
        with pytest.raises(ContractError) as exc:
            dataset_shape_from_version(version, ["context", "question"])
        message = str(exc.value)
        assert "question" in message

    def test_duplicate_within_extras_raises_naming_column(self) -> None:
        version = make_version(required_columns=["question"])
        with pytest.raises(ContractError) as exc:
            dataset_shape_from_version(version, ["context", "context"])
        assert "context" in str(exc.value)

    def test_does_not_mutate_version_required_columns(self) -> None:
        version = make_version(required_columns=["question", "answer"])
        dataset_shape_from_version(version, ["context"])
        assert version.required_columns == ["question", "answer"]


# --- dataset_shape_from_version: label schema ---------------------------------


class TestDatasetShapeLabelSchema:
    """Label schema derived field-for-field from the version's score space."""

    def test_categorical_labels_carried_through(self) -> None:
        version = make_version(score_kind=ScoreKind.CATEGORICAL, score_labels=["good", "bad"])
        _, schema = dataset_shape_from_version(version, [])
        assert isinstance(schema, LabelSchema)
        assert schema.kind is ScoreKind.CATEGORICAL
        assert schema.labels == ["good", "bad"]
        assert schema.minimum is None
        assert schema.maximum is None

    def test_numeric_with_bounds(self) -> None:
        version = numeric_version(score_minimum=1.0, score_maximum=5.0)
        _, schema = dataset_shape_from_version(version, [])
        assert schema.kind is ScoreKind.NUMERIC
        assert schema.labels is None
        assert schema.minimum == 1.0
        assert schema.maximum == 5.0

    def test_numeric_without_bounds(self) -> None:
        version = numeric_version(score_minimum=None, score_maximum=None)
        _, schema = dataset_shape_from_version(version, [])
        assert schema.kind is ScoreKind.NUMERIC
        assert schema.labels is None
        assert schema.minimum is None
        assert schema.maximum is None


# --- evaluator_seed_from_dataset ----------------------------------------------


class TestEvaluatorSeedFromDataset:
    """Deriving evaluator columns and optional score space from a dataset."""

    def test_returns_dataset_columns_unchanged(self) -> None:
        dataset = make_dataset(columns=["question", "answer", "context"])
        columns, _ = evaluator_seed_from_dataset(dataset)
        assert columns == ["question", "answer", "context"]

    def test_parses_categorical_label_schema(self) -> None:
        dataset = make_dataset(label_schema={"kind": "categorical", "labels": ["pass", "fail"]})
        _, schema = evaluator_seed_from_dataset(dataset)
        assert isinstance(schema, LabelSchema)
        assert schema.kind is ScoreKind.CATEGORICAL
        assert schema.labels == ["pass", "fail"]

    def test_parses_numeric_label_schema(self) -> None:
        dataset = make_dataset(label_schema={"kind": "numeric", "minimum": 0, "maximum": 10})
        _, schema = evaluator_seed_from_dataset(dataset)
        assert isinstance(schema, LabelSchema)
        assert schema.kind is ScoreKind.NUMERIC
        assert schema.minimum == 0
        assert schema.maximum == 10

    def test_empty_label_schema_yields_none(self) -> None:
        dataset = make_dataset(label_schema={})
        _, schema = evaluator_seed_from_dataset(dataset)
        assert schema is None

    def test_empty_label_schema_does_not_raise(self) -> None:
        dataset = make_dataset(label_schema={})
        columns, schema = evaluator_seed_from_dataset(dataset)
        assert columns == dataset.columns
        assert schema is None

    def test_does_not_mutate_dataset(self) -> None:
        dataset = make_dataset(
            columns=["question", "answer"],
            label_schema={"kind": "categorical", "labels": ["pass", "fail"]},
        )
        evaluator_seed_from_dataset(dataset)
        assert dataset.columns == ["question", "answer"]
        assert dataset.label_schema == {"kind": "categorical", "labels": ["pass", "fail"]}
