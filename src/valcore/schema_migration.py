"""Pure functions for dataset shape migration: label validation and column remapping."""

from valcore.models import DatasetRow, LabelSchema, ScoreKind


def label_matches_schema(value: str | float, schema: LabelSchema) -> bool:
    """Return True if ``value`` is a valid label under ``schema``."""
    if schema.kind is ScoreKind.CATEGORICAL:
        return isinstance(value, str) and value in (schema.labels or [])
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    below = schema.minimum is not None and value < schema.minimum
    above = schema.maximum is not None and value > schema.maximum
    return not (below or above)


def apply_column_changes(data: dict, renames: dict[str, str], columns: list[str]) -> dict:
    """Return a row's data remapped, backfilled, and pruned to ``columns``."""
    # Resolve renames against the original keys in one pass so a chain like
    # {"a": "b", "b": "c"} moves each value once rather than iteratively. Renamed
    # values are applied last so they win over an untouched pre-existing target key.
    remapped = {key: value for key, value in data.items() if key not in renames}
    for old, new in renames.items():
        if old in data:
            remapped[new] = data[old]
    return {column: remapped.get(column) for column in columns}


def invalid_label_ids(rows: list[DatasetRow], schema: LabelSchema) -> list[str]:
    """Return the ids of rows whose existing label does not validate under ``schema``."""
    invalid: list[str] = []
    for row in rows:
        if row.label is None:
            continue
        if "value" not in row.label or not label_matches_schema(row.label["value"], schema):
            invalid.append(row.id)
    return invalid
