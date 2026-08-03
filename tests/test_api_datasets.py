"""Tests for the datasets API router: upload, generate, labeling, pagination, stats."""

from collections.abc import AsyncIterator

import httpx
import pytest

from valcore.api.deps import get_store
from valcore.api.main import create_app
from valcore.datagen import GeneratedRow
from valcore.models import LabelSource, RunKind, ScoreKind
from valcore.store import Store, create_engine, init_db

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

    monkeypatch.setattr("valcore.api.routes.datasets.generate_rows", fake_generate_rows)

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


@pytest.mark.anyio
async def test_clear_label_removes_label_and_source(
    client: httpx.AsyncClient, store: Store
) -> None:
    _, row_ids = _seed_rows(store, CATEGORICAL_SCHEMA, [{"data": {"prompt": "p"}}])
    await client.patch(f"/api/datasets/rows/{row_ids[0]}", json={"label": "good"})

    resp = await client.patch(f"/api/datasets/rows/{row_ids[0]}", json={"clear_label": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
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
    # The route returns the created rows themselves, not a bare count, so the grid can
    # append without refetching.
    appended = resp.json()
    assert isinstance(appended, list)
    assert [r["data"]["prompt"] for r in appended] == ["a", "b"]
    assert all(r["id"] for r in appended)

    rows = (await client.get(f"/api/datasets/{ds_id}/rows")).json()["rows"]
    assert [r["data"]["prompt"] for r in rows] == ["a", "b"]


# -- Dataset shape edits (PATCH /{id}) ---------------------------------------


async def _create_dataset(
    client: httpx.AsyncClient,
    *,
    columns: list[str],
    label_schema: dict,
    name: str = "shape",
    description: str = "",
) -> str:
    """Create a dataset via the API and return its id."""
    resp = await client.post(
        "/api/datasets",
        json={
            "name": name,
            "description": description,
            "columns": columns,
            "label_schema": label_schema,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


@pytest.mark.anyio
async def test_patch_dataset_renames_column_remaps_every_row(client: httpx.AsyncClient) -> None:
    ds_id = await _create_dataset(client, columns=["question"], label_schema=CATEGORICAL_SCHEMA)
    await client.post(
        f"/api/datasets/{ds_id}/rows",
        json={"rows": [{"question": "q1"}, {"question": "q2"}]},
    )

    resp = await client.patch(
        f"/api/datasets/{ds_id}", json={"column_renames": {"question": "prompt"}}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["columns"] == ["prompt"]

    rows = (await client.get(f"/api/datasets/{ds_id}/rows")).json()["rows"]
    assert [r["data"] for r in rows] == [{"prompt": "q1"}, {"prompt": "q2"}]


@pytest.mark.anyio
async def test_patch_dataset_add_column_backfills_and_remove_drops(
    client: httpx.AsyncClient,
) -> None:
    ds_id = await _create_dataset(client, columns=["question"], label_schema=CATEGORICAL_SCHEMA)
    await client.post(f"/api/datasets/{ds_id}/rows", json={"rows": [{"question": "q1"}]})

    added = await client.patch(f"/api/datasets/{ds_id}", json={"columns": ["question", "answer"]})
    assert added.status_code == 200, added.text
    assert added.json()["columns"] == ["question", "answer"]
    rows = (await client.get(f"/api/datasets/{ds_id}/rows")).json()["rows"]
    assert rows[0]["data"] == {"question": "q1", "answer": None}

    removed = await client.patch(f"/api/datasets/{ds_id}", json={"columns": ["answer"]})
    assert removed.status_code == 200, removed.text
    assert removed.json()["columns"] == ["answer"]
    rows = (await client.get(f"/api/datasets/{ds_id}/rows")).json()["rows"]
    assert rows[0]["data"] == {"answer": None}


@pytest.mark.anyio
async def test_patch_dataset_unknown_rename_key_is_422(client: httpx.AsyncClient) -> None:
    ds_id = await _create_dataset(client, columns=["question"], label_schema=CATEGORICAL_SCHEMA)
    resp = await client.patch(
        f"/api/datasets/{ds_id}", json={"column_renames": {"missing": "prompt"}}
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["type"] == "ContractError"


@pytest.mark.anyio
async def test_patch_dataset_empty_body_is_noop(client: httpx.AsyncClient) -> None:
    ds_id = await _create_dataset(
        client, columns=["question"], label_schema=CATEGORICAL_SCHEMA, name="orig"
    )
    before = (await client.get(f"/api/datasets/{ds_id}")).json()

    resp = await client.patch(f"/api/datasets/{ds_id}", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json() == before


@pytest.mark.anyio
async def test_patch_dataset_narrowing_schema_with_labels_is_409(
    client: httpx.AsyncClient, store: Store
) -> None:
    ds_id, row_ids = _seed_rows(
        store,
        CATEGORICAL_SCHEMA,
        [{"data": {"prompt": "p0"}}, {"data": {"prompt": "p1"}}, {"data": {"prompt": "p2"}}],
    )
    # Two rows labeled "bad" would fall outside a schema narrowed to just "good".
    await client.patch(f"/api/datasets/rows/{row_ids[0]}", json={"label": "good"})
    await client.patch(f"/api/datasets/rows/{row_ids[1]}", json={"label": "bad"})
    await client.patch(f"/api/datasets/rows/{row_ids[2]}", json={"label": "bad"})

    narrowed = {"kind": "categorical", "labels": ["good"]}
    resp = await client.patch(f"/api/datasets/{ds_id}", json={"label_schema": narrowed})
    assert resp.status_code == 409, resp.text
    error = resp.json()["error"]
    assert error["type"] == "DestructiveChangeError"
    assert error["detail"]["invalid_label_count"] == 2

    # Nothing changed: the schema and the "bad" labels are still present.
    ds = (await client.get(f"/api/datasets/{ds_id}")).json()
    assert ds["label_schema"]["labels"] == ["good", "bad"]
    rows = (await client.get(f"/api/datasets/{ds_id}/rows")).json()["rows"]
    assert [r["label"] for r in rows] == [{"value": "good"}, {"value": "bad"}, {"value": "bad"}]


@pytest.mark.anyio
async def test_patch_dataset_narrowing_schema_with_force_clears_invalid(
    client: httpx.AsyncClient, store: Store
) -> None:
    ds_id, row_ids = _seed_rows(
        store,
        CATEGORICAL_SCHEMA,
        [{"data": {"prompt": "p0"}}, {"data": {"prompt": "p1"}}],
    )
    await client.patch(f"/api/datasets/rows/{row_ids[0]}", json={"label": "good"})
    await client.patch(f"/api/datasets/rows/{row_ids[1]}", json={"label": "bad"})

    narrowed = {"kind": "categorical", "labels": ["good"]}
    resp = await client.patch(
        f"/api/datasets/{ds_id}", json={"label_schema": narrowed, "force": True}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["label_schema"]["labels"] == ["good"]

    rows = (await client.get(f"/api/datasets/{ds_id}/rows")).json()["rows"]
    assert rows[0]["label"] == {"value": "good"}
    assert rows[1]["label"] is None
    assert rows[1]["label_source"] is None


# -- Row data edits (PATCH /rows/{row_id}) -----------------------------------


@pytest.mark.anyio
async def test_patch_row_data_merges_leaving_other_columns(
    client: httpx.AsyncClient, store: Store
) -> None:
    ds = store.create_dataset(
        name="qa", description="", columns=["question", "answer"], label_schema=CATEGORICAL_SCHEMA
    )
    rows = store.add_prepared_rows(ds.id, [{"data": {"question": "q", "answer": "a"}}])
    row_id = rows[0].id

    resp = await client.patch(f"/api/datasets/rows/{row_id}", json={"data": {"question": "new"}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"question": "new", "answer": "a"}


@pytest.mark.anyio
async def test_patch_row_data_unknown_column_is_422_and_leaves_row(
    client: httpx.AsyncClient, store: Store
) -> None:
    ds = store.create_dataset(
        name="qa", description="", columns=["question"], label_schema=CATEGORICAL_SCHEMA
    )
    rows = store.add_prepared_rows(ds.id, [{"data": {"question": "q"}}])
    row_id = rows[0].id

    resp = await client.patch(f"/api/datasets/rows/{row_id}", json={"data": {"bogus": "x"}})
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["type"] == "ContractError"

    unchanged = (await client.get(f"/api/datasets/{ds.id}/rows")).json()["rows"]
    assert unchanged[0]["data"] == {"question": "q"}


@pytest.mark.anyio
async def test_patch_row_applies_data_and_note_together(
    client: httpx.AsyncClient, store: Store
) -> None:
    ds = store.create_dataset(
        name="qa", description="", columns=["question"], label_schema=CATEGORICAL_SCHEMA
    )
    rows = store.add_prepared_rows(ds.id, [{"data": {"question": "q"}}])
    row_id = rows[0].id

    resp = await client.patch(
        f"/api/datasets/rows/{row_id}", json={"data": {"question": "new"}, "note": "checked"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"] == {"question": "new"}
    assert body["note"] == "checked"


@pytest.mark.anyio
async def test_patch_row_empty_data_preserves_note_in_same_request(
    client: httpx.AsyncClient, store: Store
) -> None:
    ds = store.create_dataset(
        name="qa", description="", columns=["question"], label_schema=CATEGORICAL_SCHEMA
    )
    rows = store.add_prepared_rows(ds.id, [{"data": {"question": "q"}}])
    row_id = rows[0].id

    resp = await client.patch(f"/api/datasets/rows/{row_id}", json={"data": {}, "note": "kept"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"] == {"question": "q"}
    assert body["note"] == "kept"


# -- Row deletion (DELETE /rows/{row_id}) ------------------------------------


@pytest.mark.anyio
async def test_delete_row_returns_204_and_removes_it(
    client: httpx.AsyncClient, store: Store
) -> None:
    ds_id, row_ids = _seed_rows(
        store, CATEGORICAL_SCHEMA, [{"data": {"prompt": "p0"}}, {"data": {"prompt": "p1"}}]
    )
    resp = await client.delete(f"/api/datasets/rows/{row_ids[0]}")
    assert resp.status_code == 204, resp.text
    assert resp.content == b""

    rows = (await client.get(f"/api/datasets/{ds_id}/rows")).json()["rows"]
    assert [r["id"] for r in rows] == [row_ids[1]]


@pytest.mark.anyio
async def test_delete_rows_route_is_not_shadowed_by_dataset_delete(
    client: httpx.AsyncClient, store: Store
) -> None:
    # A dataset whose row we target still exists after deleting the row: the literal
    # ``/rows/{row_id}`` route must win over ``/{id}``.
    ds_id, row_ids = _seed_rows(store, CATEGORICAL_SCHEMA, [{"data": {"prompt": "p0"}}])
    resp = await client.delete(f"/api/datasets/rows/{row_ids[0]}")
    assert resp.status_code == 204, resp.text

    assert (await client.get(f"/api/datasets/{ds_id}")).status_code == 200
    rows = (await client.get(f"/api/datasets/{ds_id}/rows")).json()["rows"]
    assert rows == []


# -- Referenced delete -------------------------------------------------------


def _make_referencing_run(store: Store, dataset_id: str) -> str:
    """Create an evaluator, version, and a run against ``dataset_id``; return the run id."""
    evaluator = store.create_evaluator("ev")
    version = store.create_version(
        evaluator.id,
        version_name="v1",
        model="gateway/anthropic:claude-sonnet-5",
        instructions="Judge the row.",
        prompt_template="Input: {prompt}",
        required_columns=["prompt"],
        output_fields=[
            {
                "name": "verdict",
                "type": "enum",
                "description": "good or bad",
                "enum_values": ["good", "bad"],
            }
        ],
        score_field="verdict",
        score_kind=ScoreKind.CATEGORICAL,
        score_labels=["good", "bad"],
    )
    run = store.create_run(RunKind.VALIDATION, version.id, dataset_id, concurrency=1)
    return run.id


@pytest.mark.anyio
async def test_delete_dataset_referenced_by_run_is_409_and_survives(
    client: httpx.AsyncClient, store: Store
) -> None:
    ds_id, _ = _seed_rows(store, CATEGORICAL_SCHEMA, [{"data": {"prompt": "p"}}])
    _make_referencing_run(store, ds_id)

    resp = await client.delete(f"/api/datasets/{ds_id}")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["type"] == "ReferencedError"

    assert (await client.get(f"/api/datasets/{ds_id}")).status_code == 200
