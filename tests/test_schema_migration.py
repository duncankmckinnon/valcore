"""Tests for pure dataset shape migration functions."""

from valcore.schema_migration import apply_column_changes


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
