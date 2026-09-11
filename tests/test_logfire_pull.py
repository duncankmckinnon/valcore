"""Nesting, sampling, row mapping, and stubbed query/pull for Logfire datasets."""

import json
import sys
import types
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Self

import pytest

from valcore.config import FileConfig, save_config
from valcore.errors import ConfigError, ContractError
from valcore.logfire_pull import (
    nest_trees,
    pull_records,
    query_records,
    resolve_time_window,
    sample_trees,
    trees_to_rows,
)
from valcore.models import LabelSource


def _span(span_id: str, parent: str | None, **fields: object) -> dict:
    return {"span_id": span_id, "parent_span_id": parent, **fields}


# -- nest_trees ----------------------------------------------------------------


def test_nest_trees_without_id_columns_leaves_every_row_as_a_root() -> None:
    records = [{"message": "a"}, {"message": "b"}]
    trees = nest_trees(records)
    assert trees == records
    assert all("children" not in tree for tree in trees)


def test_nest_trees_hangs_returned_children_under_their_parent() -> None:
    records = [
        _span("root", None, message="parent"),
        _span("c1", "root", message="child-1"),
        _span("c2", "root", message="child-2"),
    ]
    trees = nest_trees(records)
    assert len(trees) == 1
    assert trees[0]["span_id"] == "root"
    assert [child["span_id"] for child in trees[0]["children"]] == ["c1", "c2"]
    assert trees[0]["children"][0]["message"] == "child-1"


def test_nest_trees_nests_grandchildren() -> None:
    records = [
        _span("root", None, message="p"),
        _span("mid", "root", message="m"),
        _span("leaf", "mid", message="l"),
    ]
    trees = nest_trees(records)
    assert trees[0]["children"][0]["children"][0]["span_id"] == "leaf"


def test_nest_trees_treats_missing_parent_as_a_root() -> None:
    records = [
        _span("orphan", "absent", message="o"),
        _span("root", None, message="r"),
    ]
    trees = nest_trees(records)
    assert [tree["span_id"] for tree in trees] == ["orphan", "root"]
    assert "children" not in trees[0]
    assert "children" not in trees[1]


def test_nest_trees_preserves_query_order_of_siblings() -> None:
    records = [
        _span("root", None),
        _span("b", "root"),
        _span("a", "root"),
    ]
    trees = nest_trees(records)
    assert [child["span_id"] for child in trees[0]["children"]] == ["b", "a"]


def test_nest_trees_does_not_mutate_input() -> None:
    records = [_span("root", None), _span("c", "root")]
    snapshot = [dict(row) for row in records]
    nest_trees(records)
    assert records == snapshot


def test_nest_trees_rejects_a_parent_cycle() -> None:
    records = [_span("a", "b"), _span("b", "a")]
    with pytest.raises(ContractError, match="cycle"):
        nest_trees(records)


def test_nest_trees_rejects_duplicate_span_ids() -> None:
    records = [_span("same", None, message="one"), _span("same", None, message="two")]
    with pytest.raises(ContractError, match="Duplicate span_id"):
        nest_trees(records)


# -- sample_trees --------------------------------------------------------------


def test_sample_trees_is_deterministic_for_a_seed() -> None:
    trees = [{"span_id": str(i)} for i in range(10)]
    first = sample_trees(trees, 3, seed=7)
    second = sample_trees(trees, 3, seed=7)
    assert first == second
    assert len(first) == 3


def test_sample_trees_keeps_all_roots_when_n_meets_or_exceeds_count() -> None:
    trees = [{"span_id": "a"}, {"span_id": "b"}]
    assert sample_trees(trees, 2, seed=1) == trees
    assert sample_trees(trees, 9, seed=1) == trees


def test_sample_trees_rejects_non_positive_n() -> None:
    with pytest.raises(ContractError, match="at least 1"):
        sample_trees([{"span_id": "a"}], 0, seed=1)


# -- trees_to_rows -------------------------------------------------------------


def test_trees_to_rows_omits_children_column_when_every_root_is_a_leaf() -> None:
    columns, prepared, row_annotations = trees_to_rows(
        [{"span_id": "a", "message": "x"}, {"span_id": "b", "message": "y"}],
        ["span_id", "message"],
    )
    assert columns == ["span_id", "message"]
    assert prepared[0]["data"] == {"span_id": "a", "message": "x"}
    assert "children" not in prepared[0]["data"]
    assert row_annotations == [None, None]


def test_trees_to_rows_adds_json_children_column_when_any_root_has_descendants() -> None:
    trees = nest_trees(
        [
            _span("root", None, message="p"),
            _span("c1", "root", message="k"),
            _span("leaf", None, message="alone"),
        ]
    )
    columns, prepared, row_annotations = trees_to_rows(
        trees, ["span_id", "parent_span_id", "message"]
    )
    assert columns[-1] == "children"
    by_id = {row["data"]["span_id"]: row["data"] for row in prepared}
    kids = json.loads(by_id["root"]["children"])
    assert kids[0]["span_id"] == "c1"
    assert json.loads(by_id["leaf"]["children"]) == []
    assert row_annotations == [None, None]


def test_trees_to_rows_children_json_is_itself_a_nested_tree() -> None:
    trees = nest_trees(
        [
            _span("root", None, message="p"),
            _span("mid", "root", message="m"),
            _span("leaf", "mid", message="l"),
        ]
    )
    _, prepared, _ = trees_to_rows(trees, ["span_id", "parent_span_id", "message"])
    kids = json.loads(prepared[0]["data"]["children"])
    assert kids[0]["children"][0]["span_id"] == "leaf"


def test_trees_to_rows_rejects_sql_column_named_children() -> None:
    with pytest.raises(ContractError, match="children"):
        trees_to_rows([{"span_id": "a", "children": "nope"}], ["span_id", "children"])


def test_trees_to_rows_lifts_label_column_off_the_root_only() -> None:
    trees = nest_trees(
        [
            _span("root", None, message="p", verdict="good"),
            _span("c1", "root", message="k", verdict="bad"),
        ]
    )
    columns, prepared, row_annotations = trees_to_rows(
        trees,
        ["span_id", "parent_span_id", "message", "verdict"],
        label_column="verdict",
    )
    assert "verdict" not in columns
    assert "verdict" not in prepared[0]["data"]
    assert row_annotations[0] == {"labels": ["good"], "source": LabelSource.MANUAL}
    kids = json.loads(prepared[0]["data"]["children"])
    assert kids[0]["verdict"] == "bad"


# -- time window ---------------------------------------------------------------


def test_resolve_time_window_defaults_lower_bound_to_24_hours_ago() -> None:
    now = datetime(2026, 9, 3, 17, 0, tzinfo=UTC)
    lower, upper = resolve_time_window(None, None, now=now)
    assert lower == now - timedelta(hours=24)
    assert upper is None


def test_resolve_time_window_rejects_more_than_14_days() -> None:
    now = datetime(2026, 9, 3, tzinfo=UTC)
    with pytest.raises(ContractError, match="14 days"):
        resolve_time_window(now - timedelta(days=15), now, now=now)


# -- query_records / pull_records (stubbed client) -----------------------------


@dataclass
class _QueryRecorder:
    calls: list[dict] = field(default_factory=list)
    columns: list[dict] = field(
        default_factory=lambda: [
            {"name": "span_id"},
            {"name": "parent_span_id"},
            {"name": "message"},
        ]
    )
    rows: list[dict] = field(
        default_factory=lambda: [
            {"span_id": "root", "parent_span_id": None, "message": "parent"},
            {"span_id": "c1", "parent_span_id": "root", "message": "child"},
        ]
    )
    error: Exception | None = None
    entered: bool = False
    exited: bool = False
    constructed_with: str | None = None


def _install_stub_query_client(monkeypatch: pytest.MonkeyPatch, recorder: _QueryRecorder) -> None:
    """Replace ``logfire.query_client`` so ``query_records`` never opens a network client."""

    class StubAsyncClient:
        def __init__(self, read_token: str | None = None) -> None:
            recorder.constructed_with = read_token

        async def __aenter__(self) -> Self:
            recorder.entered = True
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
            recorder.exited = True
            return False

        async def query_json_rows(self, sql: str, **kwargs: object) -> dict:
            recorder.calls.append({"sql": sql, **kwargs})
            if recorder.error is not None:
                raise recorder.error
            return {"columns": recorder.columns, "rows": recorder.rows}

    fake = types.ModuleType("logfire.query_client")
    fake.AsyncLogfireQueryClient = StubAsyncClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "logfire.query_client", fake)


@pytest.fixture
def query_recorder(monkeypatch: pytest.MonkeyPatch) -> _QueryRecorder:
    rec = _QueryRecorder()
    _install_stub_query_client(monkeypatch, rec)
    return rec


@pytest.mark.anyio
async def test_query_records_missing_api_key_names_command_and_project_read(
    query_recorder: _QueryRecorder,
) -> None:
    with pytest.raises(ConfigError) as exc:
        await query_records("SELECT 1")
    assert "valcore config set-logfire-read-key" in str(exc.value)
    assert "project:read" in str(exc.value)
    assert query_recorder.calls == []


@pytest.mark.anyio
async def test_query_records_does_not_pass_a_client_limit(
    query_recorder: _QueryRecorder,
) -> None:
    save_config(FileConfig(logfire_api_key="lf-key"))
    await query_records("SELECT span_id FROM records LIMIT 10")
    assert "limit" not in query_recorder.calls[0]
    assert query_recorder.entered is True
    assert query_recorder.exited is True
    assert query_recorder.constructed_with == "lf-key"


@pytest.mark.anyio
async def test_query_records_empty_result_is_a_contract_error(
    query_recorder: _QueryRecorder,
) -> None:
    save_config(FileConfig(logfire_api_key="lf-key"))
    query_recorder.rows = []
    with pytest.raises(ContractError, match="no rows"):
        await query_records("SELECT 1")
    assert query_recorder.exited is True


@pytest.mark.anyio
async def test_query_records_client_failure_becomes_contract_error(
    query_recorder: _QueryRecorder,
) -> None:
    save_config(FileConfig(logfire_api_key="lf-key"))
    query_recorder.error = RuntimeError("not authorized")
    with pytest.raises(ContractError, match="not authorized"):
        await query_records("SELECT 1")
    assert query_recorder.exited is True


@pytest.mark.anyio
async def test_pull_records_nests_and_samples_stubbed_query(
    query_recorder: _QueryRecorder,
) -> None:
    save_config(FileConfig(logfire_api_key="lf-key"))
    result = await pull_records("SELECT * FROM records", sample_n=1, seed=1)
    assert result.columns[-1] == "children"
    assert len(result.prepared) == 1
    assert result.seed == 1
    assert result.sql == "SELECT * FROM records"
    assert query_recorder.calls[0]["min_timestamp"] == result.min_timestamp
    assert query_recorder.calls[0]["max_timestamp"] == result.max_timestamp
    assert "limit" not in query_recorder.calls[0]
    kids = json.loads(result.prepared[0]["data"]["children"])
    assert kids[0]["span_id"] == "c1"
