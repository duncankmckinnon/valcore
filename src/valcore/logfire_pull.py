"""Turn a Logfire SQL result into valcore dataset rows.

Push stays in :mod:`valcore.logfire_io`. This module owns the inbound path: query, nest
child spans that the result actually contained, sample top-level trees, and map them onto
``DatasetRow`` field dicts. ``query_records`` lazy-imports ``AsyncLogfireQueryClient`` so
importing this module does not require a working query client at module load; the nest /
sample / map helpers are pure and have no Logfire dependency.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from valcore import config
from valcore.errors import ConfigError, ContractError
from valcore.models import LabelSource

_SET_KEY_COMMAND = "valcore config set-logfire-key"
_CHILDREN = "children"
_SPAN_ID = "span_id"
_PARENT_SPAN_ID = "parent_span_id"
_DEFAULT_WINDOW = timedelta(hours=24)
_MAX_WINDOW = timedelta(days=14)


def nest_trees(records: list[dict]) -> list[dict]:
    """Group a flat query result into top-level trees by ``parent_span_id``.

    Only rows present in ``records`` participate: a child whose parent was not selected
    becomes a root. Missing ``span_id`` / ``parent_span_id`` columns skip nesting entirely.
    Input dicts are copied; the originals are left unchanged.
    """
    if not records:
        return []

    keys: list[str] = []
    for record in records:
        for key in record:
            if key not in keys:
                keys.append(key)
    if _SPAN_ID not in keys or _PARENT_SPAN_ID not in keys:
        return [dict(record) for record in records]

    copies = [dict(record) for record in records]
    by_id: dict[Any, dict] = {}
    for node in copies:
        span_id = node.get(_SPAN_ID)
        if span_id is None:
            continue
        if span_id in by_id:
            raise ContractError(f"Duplicate span_id {span_id!r} in query result.")
        by_id[span_id] = node

    for span_id in by_id:
        _assert_no_cycle(span_id, by_id)

    children_of: dict[Any, list[dict]] = {span_id: [] for span_id in by_id}
    attached: set[int] = set()
    for node in copies:
        parent = node.get(_PARENT_SPAN_ID)
        if parent in by_id and parent != node.get(_SPAN_ID):
            children_of[parent].append(node)
            attached.add(id(node))

    def attach(node: dict) -> dict:
        span_id = node.get(_SPAN_ID)
        kids = children_of.get(span_id, []) if span_id is not None else []
        out = dict(node)
        if kids:
            out[_CHILDREN] = [attach(child) for child in kids]
        return out

    return [attach(node) for node in copies if id(node) not in attached]


def _assert_no_cycle(span_id: Any, by_id: dict[Any, dict]) -> None:
    """Walk ``span_id``'s parent chain and raise if it loops back on itself."""
    seen: set[Any] = set()
    current = span_id
    while current in by_id:
        parent = by_id[current].get(_PARENT_SPAN_ID)
        if parent is None or parent not in by_id:
            return
        if parent in seen or parent == span_id:
            raise ContractError("Query result has a cycle in parent_span_id.")
        seen.add(parent)
        current = parent


def sample_trees(trees: list[dict], n: int, seed: int) -> list[dict]:
    """Return ``n`` trees chosen uniformly at random, or all of them when ``n`` is larger.

    ``seed`` makes a rerun identical. Sampling is over top-level trees only, so a kept
    parent always keeps the children already nested under it.
    """
    if n < 1:
        raise ContractError(f"count must be at least 1, got {n}.")
    if n >= len(trees):
        return list(trees)
    return random.Random(seed).sample(trees, n)


def trees_to_rows(
    trees: list[dict],
    columns: list[str],
    *,
    label_column: str | None = None,
) -> tuple[list[str], list[dict]]:
    """Map nested trees onto ``DatasetRow`` field dicts.

    ``children`` is added as a JSON-string column only when at least one tree has
    descendants. A ``label_column`` is lifted off the top-level row (not out of the
    children JSON). Selecting ``children`` as a SQL column is refused here so the
    reserved name cannot collide with a user column.
    """
    if _CHILDREN in columns:
        raise ContractError("SQL must not select a column named 'children'; that name is reserved.")
    if label_column is not None and label_column not in columns:
        raise ContractError(
            f"label_column {label_column!r} is not one of the SQL columns {columns}."
        )

    has_children = any(bool(tree.get(_CHILDREN)) for tree in trees)
    out_columns = [column for column in columns if column != label_column]
    if has_children:
        out_columns = [*out_columns, _CHILDREN]

    prepared: list[dict] = []
    for tree in trees:
        data = {column: tree.get(column) for column in columns if column != label_column}
        if has_children:
            data[_CHILDREN] = json.dumps(tree.get(_CHILDREN) or [], default=str, indent=2)
        fields: dict = {"data": data}
        if label_column is not None and tree.get(label_column) is not None:
            fields["label"] = {"value": tree[label_column]}
            fields["label_source"] = LabelSource.MANUAL
        prepared.append(fields)
    return out_columns, prepared


def _resolve_api_key(api_key: str | None) -> str:
    """Resolve the Logfire API key, naming the CLI command and ``project:read`` on miss."""
    if api_key is not None:
        return api_key
    stored = config.load_config().logfire_api_key
    if stored is not None:
        return stored
    raise ConfigError(
        f"No Logfire API key configured. Run '{_SET_KEY_COMMAND}' with a key that carries "
        "the project:read scope (required to query)."
    )


def resolve_time_window(
    min_timestamp: datetime | None,
    max_timestamp: datetime | None,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime | None]:
    """Fill a default lower bound and refuse a window wider than Logfire's 14-day cap.

    ``now`` is injectable so tests can pin the default 24-hour window without sleeping.
    """
    clock = now or datetime.now(UTC)
    lower = min_timestamp or (clock - _DEFAULT_WINDOW)
    if lower.tzinfo is None:
        lower = lower.replace(tzinfo=UTC)
    upper = max_timestamp
    if upper is not None and upper.tzinfo is None:
        upper = upper.replace(tzinfo=UTC)
    end = upper or clock
    if end - lower > _MAX_WINDOW:
        raise ContractError("The query window cannot exceed 14 days.")
    return lower, upper


class PullResult(BaseModel):
    """The mapped rows and the provenance fields a caller persists beside them."""

    columns: list[str]
    prepared: list[dict]
    sql: str
    sample_n: int
    seed: int
    min_timestamp: datetime
    max_timestamp: datetime | None
    label_column: str | None = None


async def query_records(
    sql: str,
    *,
    api_key: str | None = None,
    min_timestamp: datetime | None = None,
    max_timestamp: datetime | None = None,
) -> tuple[list[str], list[dict]]:
    """Run ``sql`` against Logfire and return ``(column_names, rows)``.

    Does not pass the client's ``limit`` argument: that would override a SQL ``LIMIT``.
    """
    resolved_key = _resolve_api_key(api_key)
    lower, upper = resolve_time_window(min_timestamp, max_timestamp)

    try:
        from logfire.query_client import AsyncLogfireQueryClient
    except ImportError as exc:
        raise ConfigError(
            "The 'logfire' extra is required to query Logfire. Install with 'valcore[logfire]'."
        ) from exc

    try:
        async with AsyncLogfireQueryClient(read_token=resolved_key) as client:
            result = await client.query_json_rows(
                sql=sql,
                min_timestamp=lower,
                max_timestamp=upper,
            )
    except ContractError:
        raise
    except Exception as exc:
        raise ContractError(str(exc)) from exc

    columns = [
        column["name"] if isinstance(column, dict) else str(column) for column in result["columns"]
    ]
    rows = list(result["rows"])
    if not rows:
        raise ContractError("Query returned no rows.")
    return columns, rows


async def pull_records(
    sql: str,
    sample_n: int,
    *,
    seed: int | None = None,
    min_timestamp: datetime | None = None,
    max_timestamp: datetime | None = None,
    label_column: str | None = None,
    api_key: str | None = None,
) -> PullResult:
    """Query, nest, sample, and map into prepared dataset rows."""
    lower, upper = resolve_time_window(min_timestamp, max_timestamp)
    columns, rows = await query_records(
        sql,
        api_key=api_key,
        min_timestamp=lower,
        max_timestamp=upper,
    )
    trees = nest_trees(rows)
    resolved_seed = seed if seed is not None else random.SystemRandom().randrange(2**31)
    sampled = sample_trees(trees, sample_n, resolved_seed)
    out_columns, prepared = trees_to_rows(sampled, columns, label_column=label_column)
    return PullResult(
        columns=out_columns,
        prepared=prepared,
        sql=sql,
        sample_n=sample_n,
        seed=resolved_seed,
        min_timestamp=lower,
        max_timestamp=upper,
        label_column=label_column,
    )
