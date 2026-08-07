"""Tests for the read-only overview aggregate: ``Store.overview``, the enriched
``Store.list_datasets``, and the ``GET /api/overview`` endpoint.

These cover behaviour that does not exist yet: the aggregate counts computed in grouped
SQL, defensive reading of accuracy out of the untyped ``Run.metrics`` column, per-dataset
row/label counts on the datasets list, and the new overview route.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from valcore.api.deps import get_store
from valcore.api.main import create_app
from valcore.models import LabelSource, RunKind, RunStatus, ScoreKind
from valcore.store import Store, create_engine, init_db

# -- Fixtures & shared data --------------------------------------------------

CATEGORICAL_SCHEMA: dict[str, object] = {"kind": "categorical", "labels": ["good", "bad"]}

VERSION_FIELDS: dict[str, object] = {
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
            "enum_values": ["good", "bad"],
        }
    ],
    "score_field": "verdict",
    "score_kind": ScoreKind.CATEGORICAL,
    "score_labels": ["good", "bad"],
    "capabilities": [],
    "tools": [],
}


def version_fields(**overrides: object) -> dict[str, object]:
    """Return a copy of the valid version fields with overrides applied."""
    fields = dict(VERSION_FIELDS)
    fields["output_fields"] = [dict(f) for f in VERSION_FIELDS["output_fields"]]  # type: ignore[union-attr]
    fields.update(overrides)
    return fields


@pytest.fixture
def store(tmp_path) -> Store:
    """A fresh file-backed store isolated per test (never in-memory)."""
    engine = create_engine(tmp_path / "test.db")
    init_db(engine)
    return Store(engine)


@pytest.fixture
async def client(store: Store) -> AsyncIterator[httpx.AsyncClient]:
    """An ASGI client whose store dependency is overridden with the test store."""
    app = create_app()
    app.dependency_overrides[get_store] = lambda: store
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _field(obj: Any, name: str) -> Any:
    """Read ``name`` off an overview result, tolerating a dataclass or a plain dict.

    The task allows ``Store.overview`` to return either shape, so tests must not assume
    one; this normalises attribute and mapping access.
    """
    if isinstance(obj, dict):
        return obj[name]
    return getattr(obj, name)


def _make_version(store: Store) -> str:
    """Create an evaluator with one valid version and return that version's id."""
    evaluator = store.create_evaluator("e")
    return store.create_version(evaluator.id, **version_fields()).id


def _finish_run(
    store: Store,
    version_id: str,
    dataset_id: str,
    *,
    metrics: dict | None,
    finished_at: datetime,
    status: RunStatus = RunStatus.COMPLETED,
) -> str:
    """Create a run and drive it to a terminal state with the given metrics/timestamp."""
    run = store.create_run(RunKind.EVAL, version_id, dataset_id, concurrency=1)
    store.update_run_status(run.id, status, metrics=metrics, finished_at=finished_at)
    return run.id


# -- Store.overview: empty ---------------------------------------------------


def test_overview_empty_store_reports_zeros(store: Store) -> None:
    overview = store.overview()
    assert _field(overview, "evaluator_count") == 0
    assert _field(overview, "dataset_count") == 0
    assert _field(overview, "run_count") == 0
    assert _field(overview, "total_rows") == 0
    assert _field(overview, "labeled_rows") == 0
    assert _field(overview, "best_accuracy") is None
    assert _field(overview, "latest_run") is None


# -- Store.overview: entity counts -------------------------------------------


def test_overview_counts_entities(store: Store) -> None:
    e1 = store.create_evaluator("e1")
    store.create_evaluator("e2")
    v1 = store.create_version(e1.id, **version_fields()).id

    d1 = store.create_dataset("d1", "", ["question"], CATEGORICAL_SCHEMA)
    d2 = store.create_dataset("d2", "", ["question"], CATEGORICAL_SCHEMA)
    store.create_dataset("d3", "", ["question"], CATEGORICAL_SCHEMA)

    store.create_run(RunKind.EVAL, v1, d1.id, concurrency=1)
    store.create_run(RunKind.EVAL, v1, d2.id, concurrency=1)

    overview = store.overview()
    assert _field(overview, "evaluator_count") == 2
    assert _field(overview, "dataset_count") == 3
    assert _field(overview, "run_count") == 2


# -- Store.overview: row sums ------------------------------------------------


def test_overview_sums_rows_across_datasets(store: Store) -> None:
    d1 = store.create_dataset("d1", "", ["question"], CATEGORICAL_SCHEMA)
    d2 = store.create_dataset("d2", "", ["question"], CATEGORICAL_SCHEMA)

    # d1: 3 rows, 2 labeled.
    r1 = store.add_rows(d1.id, [{"question": "a"}, {"question": "b"}, {"question": "c"}])
    store.set_label(r1[0].id, {"value": "good"}, LabelSource.MANUAL)
    store.set_label(r1[1].id, {"value": "bad"}, LabelSource.MANUAL)

    # d2: 2 rows, 1 labeled.
    r2 = store.add_rows(d2.id, [{"question": "d"}, {"question": "e"}])
    store.set_label(r2[0].id, {"value": "good"}, LabelSource.MANUAL)

    overview = store.overview()
    assert _field(overview, "total_rows") == 5
    assert _field(overview, "labeled_rows") == 3


# -- Store.overview: best_accuracy -------------------------------------------


def test_overview_best_accuracy_is_max_over_finished_runs(store: Store) -> None:
    version_id = _make_version(store)
    dataset_id = store.create_dataset("d", "", ["question"], CATEGORICAL_SCHEMA).id

    _finish_run(
        store,
        version_id,
        dataset_id,
        metrics={"accuracy": 0.6},
        finished_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    _finish_run(
        store,
        version_id,
        dataset_id,
        metrics={"accuracy": 0.9},
        finished_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    _finish_run(
        store,
        version_id,
        dataset_id,
        metrics={"accuracy": 0.7},
        finished_at=datetime(2026, 1, 3, tzinfo=UTC),
    )

    best = _field(store.overview(), "best_accuracy")
    assert isinstance(best, float)
    assert best == pytest.approx(0.9)


def test_overview_best_accuracy_ignores_unfinished_runs(store: Store) -> None:
    version_id = _make_version(store)
    dataset_id = store.create_dataset("d", "", ["question"], CATEGORICAL_SCHEMA).id

    _finish_run(
        store,
        version_id,
        dataset_id,
        metrics={"accuracy": 0.5},
        finished_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    # A still-running run reporting a higher accuracy must not count.
    running = store.create_run(RunKind.EVAL, version_id, dataset_id, concurrency=1)
    store.update_run_status(running.id, RunStatus.RUNNING, metrics={"accuracy": 0.99})

    assert _field(store.overview(), "best_accuracy") == pytest.approx(0.5)


@pytest.mark.parametrize(
    "metrics",
    [
        None,
        {},
        {"agreement": 0.9},
        {"accuracy": "high"},
        {"accuracy": None},
    ],
)
def test_overview_defensive_accuracy_does_not_raise_or_count(
    store: Store, metrics: dict | None
) -> None:
    version_id = _make_version(store)
    dataset_id = store.create_dataset("d", "", ["question"], CATEGORICAL_SCHEMA).id

    _finish_run(
        store,
        version_id,
        dataset_id,
        metrics=metrics,
        finished_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    # None of these metrics shapes carry a usable accuracy, so nothing counts and the
    # missing/invalid key must never raise.
    assert _field(store.overview(), "best_accuracy") is None


def test_overview_defensive_run_does_not_hide_valid_one(store: Store) -> None:
    version_id = _make_version(store)
    dataset_id = store.create_dataset("d", "", ["question"], CATEGORICAL_SCHEMA).id

    _finish_run(
        store,
        version_id,
        dataset_id,
        metrics=None,
        finished_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    _finish_run(
        store,
        version_id,
        dataset_id,
        metrics={"accuracy": 0.8},
        finished_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    assert _field(store.overview(), "best_accuracy") == pytest.approx(0.8)


# -- Store.overview: latest_run ----------------------------------------------


def test_overview_latest_run_is_most_recently_finished(store: Store) -> None:
    version_id = _make_version(store)
    early_ds = store.create_dataset("early", "", ["question"], CATEGORICAL_SCHEMA).id
    late_ds = store.create_dataset("late", "", ["question"], CATEGORICAL_SCHEMA).id

    _finish_run(
        store,
        version_id,
        early_ds,
        metrics={"accuracy": 0.6},
        finished_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    latest_id = _finish_run(
        store,
        version_id,
        late_ds,
        metrics={"accuracy": 0.7},
        finished_at=datetime(2026, 3, 1, tzinfo=UTC),
    )
    # A run started later but not yet finished must not be chosen.
    running = store.create_run(RunKind.EVAL, version_id, late_ds, concurrency=1)
    store.update_run_status(running.id, RunStatus.RUNNING)

    latest = _field(store.overview(), "latest_run")
    assert latest is not None
    assert _field(latest, "id") == latest_id
    assert _field(latest, "dataset_name") == "late"
    assert _field(latest, "status") == RunStatus.COMPLETED
    assert _field(latest, "accuracy") == pytest.approx(0.7)
    assert _field(latest, "finished_at") == datetime(2026, 3, 1, tzinfo=UTC)


def test_overview_latest_run_accuracy_none_when_metrics_lack_it(store: Store) -> None:
    version_id = _make_version(store)
    dataset_id = store.create_dataset("d", "", ["question"], CATEGORICAL_SCHEMA).id

    _finish_run(
        store,
        version_id,
        dataset_id,
        metrics={"agreement": 0.5},
        finished_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    latest = _field(store.overview(), "latest_run")
    assert latest is not None
    assert _field(latest, "accuracy") is None


# -- Store.list_datasets: row/label counts -----------------------------------


def _datasets_by_id(store: Store) -> dict[str, Any]:
    """Map each dataset in ``list_datasets`` output by its id, tolerating dict/attr items."""
    return {_field(ds, "id"): ds for ds in store.list_datasets()}


def test_list_datasets_reports_row_and_labeled_counts(store: Store) -> None:
    empty = store.create_dataset("empty", "", ["question"], CATEGORICAL_SCHEMA)
    full = store.create_dataset("full", "", ["question"], CATEGORICAL_SCHEMA)
    partial = store.create_dataset("partial", "", ["question"], CATEGORICAL_SCHEMA)

    full_rows = store.add_rows(full.id, [{"question": "a"}, {"question": "b"}])
    for row in full_rows:
        store.set_label(row.id, {"value": "good"}, LabelSource.MANUAL)

    partial_rows = store.add_rows(
        partial.id, [{"question": "c"}, {"question": "d"}, {"question": "e"}]
    )
    store.set_label(partial_rows[0].id, {"value": "bad"}, LabelSource.MANUAL)

    by_id = _datasets_by_id(store)

    # A dataset with no rows reports 0/0, never None.
    assert _field(by_id[empty.id], "row_count") == 0
    assert _field(by_id[empty.id], "labeled_count") == 0

    assert _field(by_id[full.id], "row_count") == 2
    assert _field(by_id[full.id], "labeled_count") == 2

    assert _field(by_id[partial.id], "row_count") == 3
    assert _field(by_id[partial.id], "labeled_count") == 1


def test_list_datasets_keeps_existing_fields(store: Store) -> None:
    ds = store.create_dataset("keep", "why", ["question"], CATEGORICAL_SCHEMA)
    only = _datasets_by_id(store)[ds.id]
    assert _field(only, "name") == "keep"
    assert _field(only, "columns") == ["question"]


# -- GET /api/overview -------------------------------------------------------


@pytest.mark.anyio
async def test_get_overview_empty(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/overview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["evaluator_count"] == 0
    assert body["dataset_count"] == 0
    assert body["run_count"] == 0
    assert body["total_rows"] == 0
    assert body["labeled_rows"] == 0
    assert body["best_accuracy"] is None
    assert body["latest_run"] is None


@pytest.mark.anyio
async def test_get_overview_populated_shape(client: httpx.AsyncClient, store: Store) -> None:
    version_id = _make_version(store)
    d1 = store.create_dataset("d1", "", ["question"], CATEGORICAL_SCHEMA)
    d2 = store.create_dataset("scored", "", ["question"], CATEGORICAL_SCHEMA)

    rows = store.add_rows(d1.id, [{"question": "a"}, {"question": "b"}])
    store.set_label(rows[0].id, {"value": "good"}, LabelSource.MANUAL)

    _finish_run(
        store,
        version_id,
        d2.id,
        metrics={"accuracy": 0.75},
        finished_at=datetime(2026, 2, 1, tzinfo=UTC),
    )

    resp = await client.get("/api/overview")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["evaluator_count"] == 1
    assert body["dataset_count"] == 2
    assert body["run_count"] == 1
    assert body["total_rows"] == 2
    assert body["labeled_rows"] == 1
    assert body["best_accuracy"] == pytest.approx(0.75)

    latest = body["latest_run"]
    assert latest is not None
    assert latest["dataset_name"] == "scored"
    assert latest["status"] == RunStatus.COMPLETED.value
    assert latest["accuracy"] == pytest.approx(0.75)
    assert "id" in latest
    assert "finished_at" in latest


# -- GET /api/datasets: enriched counts --------------------------------------


@pytest.mark.anyio
async def test_get_datasets_includes_counts(client: httpx.AsyncClient, store: Store) -> None:
    empty = store.create_dataset("empty", "", ["question"], CATEGORICAL_SCHEMA)
    partial = store.create_dataset("partial", "", ["question"], CATEGORICAL_SCHEMA)

    rows = store.add_rows(partial.id, [{"question": "a"}, {"question": "b"}])
    store.set_label(rows[0].id, {"value": "good"}, LabelSource.MANUAL)

    resp = await client.get("/api/datasets")
    assert resp.status_code == 200, resp.text
    by_id = {item["id"]: item for item in resp.json()}

    assert by_id[empty.id]["row_count"] == 0
    assert by_id[empty.id]["labeled_count"] == 0
    assert by_id[partial.id]["row_count"] == 2
    assert by_id[partial.id]["labeled_count"] == 1

    # Existing fields survive unchanged so current callers are unaffected.
    assert by_id[empty.id]["name"] == "empty"
    assert by_id[empty.id]["columns"] == ["question"]
    assert by_id[empty.id]["label_schema"] == CATEGORICAL_SCHEMA
