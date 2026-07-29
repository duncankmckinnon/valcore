"""Tests for the datasets API router: upload, generate, labeling, pagination, stats."""

from collections.abc import AsyncIterator

import httpx
import pytest

from api.deps import get_store
from api.main import create_app
from evalcore.datagen import GeneratedRow
from evalcore.models import LabelSource
from evalcore.store import Store, create_engine, init_db

CATEGORICAL_SCHEMA = {"kind": "categorical", "labels": ["good", "bad"]}
NUMERIC_SCHEMA = {"kind": "numeric", "minimum": 0, "maximum": 5}


@pytest.fixture
def store(tmp_path) -> Store:
    """A fresh file-backed store isolated per test."""
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


# -- Upload ------------------------------------------------------------------


@pytest.mark.anyio
async def test_csv_upload_without_label_column(client: httpx.AsyncClient) -> None:
    csv = b"question,answer\nWhat is 2+2?,4\nWhat is 3+3?,6\n"
    resp = await client.post(
        "/api/datasets/upload",
        files={"file": ("data.csv", csv, "text/csv")},
        data={"name": "arith"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["row_count"] == 2
    assert body["dataset"]["columns"] == ["question", "answer"]

    ds_id = body["dataset"]["id"]
    rows = (await client.get(f"/api/datasets/{ds_id}/rows")).json()["rows"]
    assert rows[0]["data"] == {"question": "What is 2+2?", "answer": "4"}
    assert rows[0]["label"] is None
    assert rows[0]["label_source"] is None


@pytest.mark.anyio
async def test_csv_upload_with_label_column(client: httpx.AsyncClient) -> None:
    csv = b"question,verdict\nWhat is 2+2?,good\nWhat is 3+3?,bad\n"
    resp = await client.post(
        "/api/datasets/upload",
        files={"file": ("data.csv", csv, "text/csv")},
        data={"name": "arith", "label_column": "verdict"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["row_count"] == 2
    # label_column is pulled out of the inferred data columns.
    assert body["dataset"]["columns"] == ["question"]

    ds_id = body["dataset"]["id"]
    rows = (await client.get(f"/api/datasets/{ds_id}/rows")).json()["rows"]
    assert rows[0]["data"] == {"question": "What is 2+2?"}
    assert rows[0]["label"] == {"value": "good"}
    assert rows[0]["label_source"] == LabelSource.MANUAL.value


@pytest.mark.anyio
async def test_csv_upload_unknown_label_column_is_422(client: httpx.AsyncClient) -> None:
    csv = b"question,answer\nq,a\n"
    resp = await client.post(
        "/api/datasets/upload",
        files={"file": ("data.csv", csv, "text/csv")},
        data={"name": "arith", "label_column": "missing"},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_jsonl_upload_with_ragged_keys(client: httpx.AsyncClient) -> None:
    jsonl = (
        b'{"a": 1, "b": 2}\n'
        b'{"a": 3, "c": 4}\n'
        b"\n"  # blank line is skipped
        b'{"b": 5, "d": 6}\n'
    )
    resp = await client.post(
        "/api/datasets/upload",
        files={"file": ("data.jsonl", jsonl, "application/json")},
        data={"name": "ragged"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["row_count"] == 3
    # Union of keys, in first-seen order.
    assert body["dataset"]["columns"] == ["a", "b", "c", "d"]

    ds_id = body["dataset"]["id"]
    rows = (await client.get(f"/api/datasets/{ds_id}/rows")).json()["rows"]
    assert rows[0]["data"] == {"a": 1, "b": 2}
    assert rows[1]["data"] == {"a": 3, "c": 4}


@pytest.mark.anyio
async def test_empty_file_is_422(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/datasets/upload",
        files={"file": ("data.csv", b"question,answer\n", "text/csv")},
        data={"name": "empty"},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_oversized_file_is_422(client: httpx.AsyncClient) -> None:
    oversized = b"x" * (10 * 1024 * 1024 + 1)
    resp = await client.post(
        "/api/datasets/upload",
        files={"file": ("data.csv", oversized, "text/csv")},
        data={"name": "big"},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_unsupported_extension_is_422(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/datasets/upload",
        files={"file": ("data.txt", b"whatever", "text/plain")},
        data={"name": "txt"},
    )
    assert resp.status_code == 422


# -- Generate ----------------------------------------------------------------


@pytest.mark.anyio
async def test_generate_persists_rows_with_generated_source(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    async def fake_generate_rows(description, columns, label_schema, count):
        return [
            GeneratedRow(
                data={"prompt": f"p{i}"},
                suggested_label="good" if i % 2 == 0 else "bad",
                reasoning=f"reason {i}",
            )
            for i in range(count)
        ]

    monkeypatch.setattr("api.routes.datasets.generate_rows", fake_generate_rows)

    resp = await client.post(
        "/api/datasets/generate",
        json={
            "name": "gen",
            "description": "some data",
            "columns": ["prompt"],
            "label_schema": CATEGORICAL_SCHEMA,
            "count": 3,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["row_count"] == 3

    ds_id = body["dataset"]["id"]
    rows = (await client.get(f"/api/datasets/{ds_id}/rows")).json()["rows"]
    assert len(rows) == 3
    for row in rows:
        assert row["label_source"] == LabelSource.GENERATED.value
        assert row["suggested_label"] is not None
        assert row["label_reasoning"].startswith("reason")
        assert row["label"] is None  # suggested, not yet applied
    assert rows[0]["suggested_label"] == {"value": "good"}


@pytest.mark.anyio
async def test_generate_count_over_cap_is_422(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/datasets/generate",
        json={
            "name": "gen",
            "description": "d",
            "columns": ["prompt"],
            "label_schema": CATEGORICAL_SCHEMA,
            "count": 201,
        },
    )
    assert resp.status_code == 422


# -- Labeling ----------------------------------------------------------------


def _seed_rows(store: Store, label_schema: dict, prepared: list[dict]) -> tuple[str, list[str]]:
    """Create a dataset with prepared rows, returning its id and row ids."""
    ds = store.create_dataset(
        name="seed", description="", columns=["prompt"], label_schema=label_schema
    )
    rows = store.add_prepared_rows(ds.id, prepared)
    return ds.id, [r.id for r in rows]


@pytest.mark.anyio
async def test_accept_suggestion_sets_accepted_and_copies_value(
    client: httpx.AsyncClient, store: Store
) -> None:
    _, row_ids = _seed_rows(
        store,
        CATEGORICAL_SCHEMA,
        [
            {
                "data": {"prompt": "p"},
                "suggested_label": {"value": "good"},
                "label_reasoning": "r",
                "label_source": LabelSource.GENERATED,
            }
        ],
    )
    resp = await client.patch(f"/api/datasets/rows/{row_ids[0]}", json={"accept_suggestion": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["label"] == {"value": "good"}
    assert body["label_source"] == LabelSource.ACCEPTED.value


@pytest.mark.anyio
async def test_accept_suggestion_without_suggestion_is_422(
    client: httpx.AsyncClient, store: Store
) -> None:
    _, row_ids = _seed_rows(store, CATEGORICAL_SCHEMA, [{"data": {"prompt": "p"}}])
    resp = await client.patch(f"/api/datasets/rows/{row_ids[0]}", json={"accept_suggestion": True})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_explicit_label_sets_manual(client: httpx.AsyncClient, store: Store) -> None:
    _, row_ids = _seed_rows(store, CATEGORICAL_SCHEMA, [{"data": {"prompt": "p"}}])
    resp = await client.patch(f"/api/datasets/rows/{row_ids[0]}", json={"label": "bad"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["label"] == {"value": "bad"}
    assert body["label_source"] == LabelSource.MANUAL.value


@pytest.mark.anyio
async def test_explicit_numeric_label_sets_manual(client: httpx.AsyncClient, store: Store) -> None:
    _, row_ids = _seed_rows(store, NUMERIC_SCHEMA, [{"data": {"prompt": "p"}}])
    resp = await client.patch(f"/api/datasets/rows/{row_ids[0]}", json={"label": 3})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["label"] == {"value": 3}
    assert body["label_source"] == LabelSource.MANUAL.value


@pytest.mark.anyio
async def test_out_of_schema_categorical_label_is_422(
    client: httpx.AsyncClient, store: Store
) -> None:
    _, row_ids = _seed_rows(store, CATEGORICAL_SCHEMA, [{"data": {"prompt": "p"}}])
    resp = await client.patch(f"/api/datasets/rows/{row_ids[0]}", json={"label": "maybe"})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_out_of_schema_numeric_label_is_422(client: httpx.AsyncClient, store: Store) -> None:
    _, row_ids = _seed_rows(store, NUMERIC_SCHEMA, [{"data": {"prompt": "p"}}])
    resp = await client.patch(f"/api/datasets/rows/{row_ids[0]}", json={"label": 99})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_patch_note_only_leaves_label_untouched(
    client: httpx.AsyncClient, store: Store
) -> None:
    _, row_ids = _seed_rows(store, CATEGORICAL_SCHEMA, [{"data": {"prompt": "p"}}])
    resp = await client.patch(f"/api/datasets/rows/{row_ids[0]}", json={"note": "hmm"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["note"] == "hmm"
    assert body["label"] is None
    assert body["label_source"] is None


# -- Pagination --------------------------------------------------------------


@pytest.mark.anyio
async def test_pagination_returns_the_right_slice(client: httpx.AsyncClient, store: Store) -> None:
    ds_id, _ = _seed_rows(
        store, CATEGORICAL_SCHEMA, [{"data": {"prompt": f"p{i}"}} for i in range(5)]
    )
    resp = await client.get(f"/api/datasets/{ds_id}/rows", params={"limit": 2, "offset": 1})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 1
    assert [r["idx"] for r in body["rows"]] == [1, 2]
    assert [r["data"]["prompt"] for r in body["rows"]] == ["p1", "p2"]


# -- Stats -------------------------------------------------------------------


@pytest.mark.anyio
async def test_stats_counts_are_correct(client: httpx.AsyncClient, store: Store) -> None:
    ds_id, row_ids = _seed_rows(
        store, CATEGORICAL_SCHEMA, [{"data": {"prompt": f"p{i}"}} for i in range(4)]
    )
    # Label three of four rows: two "good", one "bad".
    await client.patch(f"/api/datasets/rows/{row_ids[0]}", json={"label": "good"})
    await client.patch(f"/api/datasets/rows/{row_ids[1]}", json={"label": "good"})
    await client.patch(f"/api/datasets/rows/{row_ids[2]}", json={"label": "bad"})

    resp = await client.get(f"/api/datasets/{ds_id}/stats")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 4
    assert body["labeled"] == 3
    assert body["unlabeled"] == 1
    assert body["label_distribution"] == {"good": 2, "bad": 1}


# -- CRUD --------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_get_list_delete_roundtrip(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/datasets",
        json={
            "name": "manual",
            "description": "hand made",
            "columns": ["prompt"],
            "label_schema": CATEGORICAL_SCHEMA,
        },
    )
    assert created.status_code == 200, created.text
    ds_id = created.json()["id"]

    got = await client.get(f"/api/datasets/{ds_id}")
    assert got.status_code == 200
    assert got.json()["name"] == "manual"

    listed = await client.get("/api/datasets")
    assert [d["id"] for d in listed.json()] == [ds_id]

    deleted = await client.delete(f"/api/datasets/{ds_id}")
    assert deleted.status_code == 200
    assert (await client.get(f"/api/datasets/{ds_id}")).status_code == 404


@pytest.mark.anyio
async def test_append_rows(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/datasets",
        json={
            "name": "manual",
            "description": "",
            "columns": ["prompt"],
            "label_schema": CATEGORICAL_SCHEMA,
        },
    )
    ds_id = created.json()["id"]
    resp = await client.post(
        f"/api/datasets/{ds_id}/rows",
        json={"rows": [{"prompt": "a"}, {"prompt": "b"}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["row_count"] == 2
    rows = (await client.get(f"/api/datasets/{ds_id}/rows")).json()["rows"]
    assert [r["data"]["prompt"] for r in rows] == ["a", "b"]
