"""Pure functions for dataset shape migration: column remapping."""


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
