"""Tests for pure dataset shape migration functions."""

from valcore.models import DatasetRow, LabelSchema, ScoreKind
from valcore.schema_migration import (
    apply_column_changes,
    invalid_label_ids,
    label_matches_schema,
)


def categorical_schema(labels: list[str] | None = None) -> LabelSchema:
    """Build a categorical LabelSchema over ``labels``."""
    return LabelSchema(kind=ScoreKind.CATEGORICAL, labels=labels or ["good", "bad"])


def numeric_schema(minimum: float | None = None, maximum: float | None = None) -> LabelSchema:
    """Build a numeric LabelSchema with the given bounds."""
    return LabelSchema(kind=ScoreKind.NUMERIC, minimum=minimum, maximum=maximum)


def make_row(row_id: str, label: dict | None) -> DatasetRow:
    """Build a DatasetRow carrying ``label`` under id ``row_id``."""
    return DatasetRow(id=row_id, dataset_id="ds1", idx=0, data={}, label=label)


class TestLabelMatchesSchema:
    """Categorical and numeric validation of a single label value."""

    def test_categorical_hit(self) -> None:
        assert label_matches_schema("good", categorical_schema(["good", "bad"])) is True

    def test_categorical_miss(self) -> None:
        assert label_matches_schema("ugly", categorical_schema(["good", "bad"])) is False

    def test_categorical_rejects_number(self) -> None:
        assert label_matches_schema(1, categorical_schema(["good", "bad"])) is False

    def test_numeric_in_range(self) -> None:
        assert label_matches_schema(5, numeric_schema(0, 10)) is True

    def test_numeric_below_min(self) -> None:
        assert label_matches_schema(-1, numeric_schema(0, 10)) is False

    def test_numeric_above_max(self) -> None:
        assert label_matches_schema(11, numeric_schema(0, 10)) is False

    def test_numeric_at_bounds_inclusive(self) -> None:
        assert label_matches_schema(0, numeric_schema(0, 10)) is True
        assert label_matches_schema(10, numeric_schema(0, 10)) is True

    def test_numeric_unbounded(self) -> None:
        assert label_matches_schema(1_000_000, numeric_schema()) is True
        assert label_matches_schema(-1_000_000, numeric_schema()) is True

    def test_numeric_rejects_bool(self) -> None:
        assert label_matches_schema(True, numeric_schema()) is False

    def test_numeric_rejects_string(self) -> None:
        assert label_matches_schema("5", numeric_schema(0, 10)) is False

    def test_numeric_float_in_range(self) -> None:
        assert label_matches_schema(2.5, numeric_schema(0, 10)) is True

    def test_numeric_only_minimum_bound(self) -> None:
        assert label_matches_schema(5, numeric_schema(minimum=0)) is True
        assert label_matches_schema(-1, numeric_schema(minimum=0)) is False
        assert label_matches_schema(1_000, numeric_schema(minimum=0)) is True

    def test_numeric_only_maximum_bound(self) -> None:
        assert label_matches_schema(5, numeric_schema(maximum=10)) is True
        assert label_matches_schema(11, numeric_schema(maximum=10)) is False
        assert label_matches_schema(-1_000, numeric_schema(maximum=10)) is True


class TestApplyColumnChanges:
    """Rename, backfill, and prune of a row's data dict."""

    def test_pure_rename(self) -> None:
        result = apply_column_changes({"a": 1}, {"a": "b"}, ["b"])
        assert result == {"b": 1}

    def test_rename_chain_resolved_against_originals(self) -> None:
        result = apply_column_changes({"a": 1, "b": 2}, {"a": "b", "b": "c"}, ["b", "c"])
        assert result == {"b": 1, "c": 2}

    def test_addition_backfills_none(self) -> None:
        result = apply_column_changes({"a": 1}, {}, ["a", "b"])
        assert result == {"a": 1, "b": None}

    def test_removal_drops_key(self) -> None:
        result = apply_column_changes({"a": 1, "b": 2}, {}, ["a"])
        assert result == {"a": 1}

    def test_combined_rename_add_remove(self) -> None:
        result = apply_column_changes({"a": 1, "gone": 9}, {"a": "b"}, ["b", "new"])
        assert result == {"b": 1, "new": None}

    def test_rename_collides_with_existing_column(self) -> None:
        # The renamed value wins over the pre-existing target key.
        result = apply_column_changes({"a": 1, "b": 2}, {"a": "b"}, ["b"])
        assert result == {"b": 1}

    def test_input_not_mutated(self) -> None:
        data = {"a": 1}
        apply_column_changes(data, {"a": "b"}, ["b", "c"])
        assert data == {"a": 1}

    def test_rename_of_absent_key_backfills_target(self) -> None:
        # An old name that isn't in ``data`` produces no value; the target column,
        # being in ``columns``, is still backfilled to None.
        result = apply_column_changes({"a": 1}, {"missing": "y"}, ["a", "y"])
        assert result == {"a": 1, "y": None}

    def test_empty_columns_yields_empty(self) -> None:
        assert apply_column_changes({"a": 1, "b": 2}, {}, []) == {}

    def test_empty_renames_still_backfills_and_prunes(self) -> None:
        result = apply_column_changes({"a": 1, "drop": 2}, {}, ["a", "add"])
        assert result == {"a": 1, "add": None}

    def test_result_key_order_follows_columns(self) -> None:
        result = apply_column_changes({"a": 1, "b": 2, "c": 3}, {}, ["c", "a", "b"])
        assert list(result.keys()) == ["c", "a", "b"]


class TestInvalidLabelIds:
    """Identify rows whose stored label no longer validates."""

    def test_unlabeled_rows_never_returned(self) -> None:
        rows = [make_row("r1", None), make_row("r2", None)]
        assert invalid_label_ids(rows, categorical_schema()) == []

    def test_categorical_label_removed_from_schema_is_returned(self) -> None:
        rows = [make_row("r1", {"value": "obsolete"})]
        assert invalid_label_ids(rows, categorical_schema(["good", "bad"])) == ["r1"]

    def test_numeric_label_outside_tightened_bounds_is_returned(self) -> None:
        rows = [make_row("r1", {"value": 15})]
        assert invalid_label_ids(rows, numeric_schema(0, 10)) == ["r1"]

    def test_still_valid_label_is_not_returned(self) -> None:
        rows = [make_row("r1", {"value": "good"})]
        assert invalid_label_ids(rows, categorical_schema(["good", "bad"])) == []

    def test_label_dict_missing_value_key_is_returned(self) -> None:
        rows = [make_row("r1", {"reasoning": "no value here"})]
        assert invalid_label_ids(rows, categorical_schema(["good", "bad"])) == ["r1"]

    def test_returns_only_invalid_ids_in_order(self) -> None:
        rows = [
            make_row("r1", {"value": "good"}),
            make_row("r2", {"value": "obsolete"}),
            make_row("r3", None),
            make_row("r4", {"value": "bad"}),
            make_row("r5", {"value": "gone"}),
        ]
        assert invalid_label_ids(rows, categorical_schema(["good", "bad"])) == ["r2", "r5"]

    def test_numeric_unbounded_rejects_non_numeric_label(self) -> None:
        rows = [make_row("r1", {"value": "not a number"}), make_row("r2", {"value": 42})]
        assert invalid_label_ids(rows, numeric_schema()) == ["r1"]
