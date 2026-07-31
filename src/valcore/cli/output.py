"""Plain-text table rendering and the JSON-or-table ``emit`` helper.

No ``rich`` and no extra dependency: aligned columns are computed by hand. JSON
output is ``json.dumps(..., indent=2, default=str)`` so datetimes, enums, and
``Path`` values serialize without bespoke encoders.
"""

import json
from typing import Any

import click


def _cell(value: Any) -> str:
    """Render one table cell; ``None`` becomes an empty string."""
    if value is None:
        return ""
    return str(value)


def table(rows: list[dict], columns: list[str]) -> str:
    """Render ``rows`` as an aligned, header-topped plain-text table.

    Only ``columns`` are shown, in order. Column widths fit the widest cell
    (including the header). An empty ``rows`` still renders the header line.
    """
    cells = [{c: _cell(row.get(c)) for c in columns} for row in rows]
    widths = {c: len(c) for c in columns}
    for cell_row in cells:
        for c in columns:
            widths[c] = max(widths[c], len(cell_row[c]))

    def render(values: dict[str, str]) -> str:
        return "  ".join(values[c].ljust(widths[c]) for c in columns).rstrip()

    header = render({c: c for c in columns})
    separator = "  ".join("-" * widths[c] for c in columns).rstrip()
    lines = [header, separator, *(render(cell_row) for cell_row in cells)]
    return "\n".join(lines)


def emit(data: Any, as_json: bool, columns: list[str] | None = None) -> None:
    """Write ``data`` to stdout as JSON when ``as_json`` else as a table.

    ``data`` is either a list of dicts or a single dict. When ``columns`` is
    ``None`` the union of keys (order preserved) is used for the table.
    """
    if as_json:
        click.echo(json.dumps(data, indent=2, default=str))
        return

    rows = data if isinstance(data, list) else [data]
    if columns is None:
        columns = list(rows[0].keys()) if rows else []
    click.echo(table(rows, columns))
