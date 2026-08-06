"""Tests for the datasets API router: upload, generate, labeling, pagination, stats."""

from collections.abc import AsyncIterator

import httpx
import pytest

from valcore.api.deps import get_store
from valcore.api.main import create_app
from valcore.datagen import GeneratedRow
from valcore.models import LabelSource, RunKind, ScoreKind, check_dataset_compatibility
from valcore.store import EvaluatorVersion, Store, create_engine, init_db

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
    async def fake_generate_rows(description, columns, label_schema, count, **kwargs):
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


# -- Generate: description vs instructions -----------------------------------


def _install_recording_generate(monkeypatch, calls: list[dict]) -> None:
    """Stub ``generate_rows`` so it records each call and never touches a model.

    The stub returns one row per requested item, filling every column so the persisted
    rows match the derived shape. It suggests a label only when a ``label_schema`` is
    passed, mirroring how the handler asks for labels only when it wants them.
    """

    async def fake_generate_rows(
        description,
        columns,
        label_schema,
        count,
        *,
        column_notes=None,
        label_guidance=None,
        label_mix=None,
        model=None,
        agent=None,
    ):
        calls.append(
            {
                "description": description,
                "columns": list(columns),
                "label_schema": label_schema,
                "count": count,
                "column_notes": column_notes,
                "label_guidance": label_guidance,
                "label_mix": label_mix,
            }
        )
        return [
            GeneratedRow(
                data={col: f"{col}{i}" for col in columns},
                suggested_label="good" if label_schema is not None else None,
                reasoning=f"reason {i}",
            )
            for i in range(count)
        ]

    monkeypatch.setattr("valcore.api.routes.datasets.generate_rows", fake_generate_rows)


@pytest.mark.anyio
async def test_generate_without_instructions_uses_description_for_generation(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    # Backward compatible: with no ``instructions``, ``description`` drives generation
    # and is also what gets stored, exactly as before this field existed.
    calls: list[dict] = []
    _install_recording_generate(monkeypatch, calls)

    resp = await client.post(
        "/api/datasets/generate",
        json={
            "name": "gen",
            "description": "some data",
            "columns": ["prompt"],
            "label_schema": CATEGORICAL_SCHEMA,
            "count": 2,
        },
    )
    assert resp.status_code == 200, resp.text
    assert calls[0]["description"] == "some data"
    assert resp.json()["dataset"]["description"] == "some data"


@pytest.mark.anyio
async def test_generate_with_instructions_drives_generation_and_stores_description(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    # When present, ``instructions`` steer generation while ``description`` is only stored.
    calls: list[dict] = []
    _install_recording_generate(monkeypatch, calls)

    resp = await client.post(
        "/api/datasets/generate",
        json={
            "name": "gen",
            "description": "Stored on the dataset.",
            "instructions": "Adversarial refusal cases across languages.",
            "columns": ["prompt"],
            "label_schema": CATEGORICAL_SCHEMA,
            "count": 2,
        },
    )
    assert resp.status_code == 200, resp.text
    assert calls[0]["description"] == "Adversarial refusal cases across languages."
    assert resp.json()["dataset"]["description"] == "Stored on the dataset."


# -- Generate from evaluator version -----------------------------------------


def _make_version(
    store: Store,
    *,
    required_columns: list[str] | None = None,
    score_kind: ScoreKind = ScoreKind.CATEGORICAL,
    score_labels: list[str] | None = None,
) -> EvaluatorVersion:
    """Create an evaluator and a single version to seed dataset generation from."""
    required_columns = required_columns if required_columns is not None else ["prompt", "response"]
    score_labels = score_labels if score_labels is not None else ["good", "bad"]
    evaluator = store.create_evaluator("ev")
    return store.create_version(
        evaluator.id,
        version_name="v1",
        model="gateway/anthropic:claude-sonnet-5",
        instructions="Judge the row.",
        prompt_template="Prompt: {prompt}\nResponse: {response}",
        required_columns=required_columns,
        output_fields=[
            {
                "name": "verdict",
                "type": "enum",
                "description": "good or bad",
                "enum_values": score_labels,
            }
        ],
        score_field="verdict",
        score_kind=score_kind,
        score_labels=score_labels,
    )


@pytest.mark.anyio
async def test_generate_from_version_columns_are_required_then_extras(
    client: httpx.AsyncClient, store: Store, monkeypatch
) -> None:
    version = _make_version(store)
    calls: list[dict] = []
    _install_recording_generate(monkeypatch, calls)

    resp = await client.post(
        "/api/datasets/generate-from-version",
        json={
            "version_id": version.id,
            "name": "multilingual-refusals",
            "instructions": "Adversarial refusal cases across languages.",
            "extra_columns": ["locale"],
            "count": 3,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["row_count"] == 3
    # Required columns first, in order, then the extras.
    assert body["dataset"]["columns"] == ["prompt", "response", "locale"]
    # The shape, not the stored description, is what the generator receives.
    assert calls[0]["columns"] == ["prompt", "response", "locale"]


@pytest.mark.anyio
async def test_generate_from_version_produces_a_compatible_dataset(
    client: httpx.AsyncClient, store: Store, monkeypatch
) -> None:
    version = _make_version(store)
    _install_recording_generate(monkeypatch, [])

    resp = await client.post(
        "/api/datasets/generate-from-version",
        json={
            "version_id": version.id,
            "name": "seeded",
            "count": 2,
        },
    )
    assert resp.status_code == 200, resp.text
    ds_id = resp.json()["dataset"]["id"]

    # The derived shape must satisfy the compatibility check by construction, so a run
    # against the source version would start.
    dataset = store.get_dataset(ds_id)
    check_dataset_compatibility(store.get_version(version.id), dataset)

    rows = (await client.get(f"/api/datasets/{ds_id}/rows")).json()["rows"]
    for row in rows:
        assert row["label_source"] == LabelSource.GENERATED.value
        assert row["suggested_label"] is not None


@pytest.mark.anyio
async def test_generate_from_version_without_labels_leaves_no_ground_truth(
    client: httpx.AsyncClient, store: Store, monkeypatch
) -> None:
    version = _make_version(store)
    calls: list[dict] = []
    _install_recording_generate(monkeypatch, calls)

    resp = await client.post(
        "/api/datasets/generate-from-version",
        json={
            "version_id": version.id,
            "name": "no-labels",
            "include_labels": False,
            "count": 2,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ds_id = body["dataset"]["id"]

    # An empty label schema is the legal "no ground truth" state.
    assert body["dataset"]["label_schema"] == {}
    # The generator is told there is no label space to fill.
    assert calls[0]["label_schema"] is None

    rows = (await client.get(f"/api/datasets/{ds_id}/rows")).json()["rows"]
    for row in rows:
        assert row["suggested_label"] is None

    # Without labels it is still immediately runnable against the source version.
    check_dataset_compatibility(store.get_version(version.id), store.get_dataset(ds_id))


@pytest.mark.anyio
async def test_generate_from_version_passes_column_notes_and_label_guidance(
    client: httpx.AsyncClient, store: Store, monkeypatch
) -> None:
    version = _make_version(store)
    calls: list[dict] = []
    _install_recording_generate(monkeypatch, calls)

    resp = await client.post(
        "/api/datasets/generate-from-version",
        json={
            "version_id": version.id,
            "name": "guided",
            "extra_columns": ["locale"],
            "column_notes": {"prompt": "a", "response": "b", "locale": "c"},
            "include_labels": True,
            "label_guidance": "partial compliance is borderline, not bad",
            "count": 1,
        },
    )
    assert resp.status_code == 200, resp.text
    assert calls[0]["column_notes"] == {"prompt": "a", "response": "b", "locale": "c"}
    assert calls[0]["label_guidance"] == "partial compliance is borderline, not bad"


@pytest.mark.anyio
async def test_generate_from_version_unknown_column_note_key_is_422(
    client: httpx.AsyncClient, store: Store, monkeypatch
) -> None:
    version = _make_version(store)
    _install_recording_generate(monkeypatch, [])

    resp = await client.post(
        "/api/datasets/generate-from-version",
        json={
            "version_id": version.id,
            "name": "typo",
            "column_notes": {"prmpt": "oops"},
            "count": 1,
        },
    )
    assert resp.status_code == 422, resp.text
    error = resp.json()["error"]
    assert error["type"] == "ContractError"
    # The unknown key is named so a typo is never silently ignored.
    assert "prmpt" in error["message"]


@pytest.mark.anyio
async def test_generate_from_version_extra_column_duplicating_required_is_422(
    client: httpx.AsyncClient, store: Store, monkeypatch
) -> None:
    version = _make_version(store)
    _install_recording_generate(monkeypatch, [])

    resp = await client.post(
        "/api/datasets/generate-from-version",
        json={
            "version_id": version.id,
            "name": "clash",
            "extra_columns": ["prompt"],
            "count": 1,
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["type"] == "ContractError"


@pytest.mark.anyio
async def test_generate_from_version_count_over_cap_is_422(
    client: httpx.AsyncClient, store: Store, monkeypatch
) -> None:
    version = _make_version(store)
    _install_recording_generate(monkeypatch, [])

    resp = await client.post(
        "/api/datasets/generate-from-version",
        json={
            "version_id": version.id,
            "name": "toomany",
            "count": 201,
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["type"] == "ContractError"


@pytest.mark.anyio
async def test_generate_from_version_label_guidance_without_labels_is_422(
    client: httpx.AsyncClient, store: Store, monkeypatch
) -> None:
    version = _make_version(store)
    _install_recording_generate(monkeypatch, [])

    resp = await client.post(
        "/api/datasets/generate-from-version",
        json={
            "version_id": version.id,
            "name": "contradiction",
            "include_labels": False,
            "label_guidance": "how to assign labels",
            "count": 1,
        },
    )
    # Silently dropping the guidance would mislead the user, so it must raise.
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["type"] == "ContractError"


@pytest.mark.anyio
async def test_generate_from_version_unknown_version_is_404(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    _install_recording_generate(monkeypatch, [])

    resp = await client.post(
        "/api/datasets/generate-from-version",
        json={
            "version_id": "ver_missing",
            "name": "orphan",
            "count": 1,
        },
    )
    assert resp.status_code == 404, resp.text


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


# -- Generate: prescribed label mix ------------------------------------------


@pytest.mark.anyio
async def test_generate_passes_label_mix_through(client: httpx.AsyncClient, monkeypatch) -> None:
    calls: list[dict] = []
    _install_recording_generate(monkeypatch, calls)

    resp = await client.post(
        "/api/datasets/generate",
        json={
            "name": "mixed",
            "description": "some data",
            "columns": ["prompt"],
            "label_schema": CATEGORICAL_SCHEMA,
            "label_mix": {"good": 0.25, "bad": 0.75},
            "count": 4,
        },
    )
    assert resp.status_code == 200, resp.text
    assert calls[0]["label_mix"] == {"good": 0.25, "bad": 0.75}


@pytest.mark.anyio
async def test_generate_without_label_mix_leaves_distribution_to_the_prompt(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    calls: list[dict] = []
    _install_recording_generate(monkeypatch, calls)

    resp = await client.post(
        "/api/datasets/generate",
        json={
            "name": "unmixed",
            "description": "some data",
            "columns": ["prompt"],
            "label_schema": CATEGORICAL_SCHEMA,
            "count": 2,
        },
    )
    assert resp.status_code == 200, resp.text
    assert calls[0]["label_mix"] is None


@pytest.mark.anyio
async def test_generate_label_mix_with_unknown_label_is_422(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/api/datasets/generate",
        json={
            "name": "typo",
            "description": "d",
            "columns": ["prompt"],
            "label_schema": CATEGORICAL_SCHEMA,
            "label_mix": {"good": 0.5, "goood": 0.5},
            "count": 4,
        },
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.anyio
async def test_generate_label_mix_not_summing_to_one_is_422(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/api/datasets/generate",
        json={
            "name": "lopsided",
            "description": "d",
            "columns": ["prompt"],
            "label_schema": CATEGORICAL_SCHEMA,
            "label_mix": {"good": 0.5, "bad": 0.2},
            "count": 4,
        },
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.anyio
async def test_generate_label_mix_on_numeric_schema_is_422(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/api/datasets/generate",
        json={
            "name": "numeric",
            "description": "d",
            "columns": ["prompt"],
            "label_schema": NUMERIC_SCHEMA,
            "label_mix": {"3": 1.0},
            "count": 4,
        },
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.anyio
async def test_generate_from_version_passes_label_mix_through(
    client: httpx.AsyncClient, store: Store, monkeypatch
) -> None:
    version = _make_version(store)
    calls: list[dict] = []
    _install_recording_generate(monkeypatch, calls)

    resp = await client.post(
        "/api/datasets/generate-from-version",
        json={
            "version_id": version.id,
            "name": "mixed",
            "include_labels": True,
            "label_mix": {"good": 0.5, "bad": 0.5},
            "count": 2,
        },
    )
    assert resp.status_code == 200, resp.text
    assert calls[0]["label_mix"] == {"good": 0.5, "bad": 0.5}


@pytest.mark.anyio
async def test_generate_from_version_label_mix_without_labels_is_422(
    client: httpx.AsyncClient, store: Store, monkeypatch
) -> None:
    # A distribution over labels contradicts asking for no labels; refuse rather than drop it.
    version = _make_version(store)
    _install_recording_generate(monkeypatch, [])

    resp = await client.post(
        "/api/datasets/generate-from-version",
        json={
            "version_id": version.id,
            "name": "contradiction",
            "include_labels": False,
            "label_mix": {"good": 1.0},
            "count": 2,
        },
    )
    assert resp.status_code == 422, resp.text


# -- Generate: per-column notes ----------------------------------------------


@pytest.mark.anyio
async def test_generate_passes_column_notes_through(client: httpx.AsyncClient, monkeypatch) -> None:
    calls: list[dict] = []
    _install_recording_generate(monkeypatch, calls)

    resp = await client.post(
        "/api/datasets/generate",
        json={
            "name": "noted",
            "description": "some data",
            "columns": ["question", "answer"],
            "column_notes": {"question": "a support ticket", "answer": "the agent reply"},
            "label_schema": CATEGORICAL_SCHEMA,
            "count": 2,
        },
    )
    assert resp.status_code == 200, resp.text
    assert calls[0]["column_notes"] == {
        "question": "a support ticket",
        "answer": "the agent reply",
    }


@pytest.mark.anyio
async def test_generate_without_column_notes_sends_none(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    calls: list[dict] = []
    _install_recording_generate(monkeypatch, calls)

    resp = await client.post(
        "/api/datasets/generate",
        json={
            "name": "plain",
            "description": "some data",
            "columns": ["question"],
            "label_schema": CATEGORICAL_SCHEMA,
            "count": 1,
        },
    )
    assert resp.status_code == 200, resp.text
    assert calls[0]["column_notes"] is None


@pytest.mark.anyio
async def test_generate_unknown_column_note_key_is_422(client: httpx.AsyncClient) -> None:
    # A note on a column the dataset will not have is a typo, not a silent no-op.
    resp = await client.post(
        "/api/datasets/generate",
        json={
            "name": "typo",
            "description": "d",
            "columns": ["question"],
            "column_notes": {"quesiton": "oops"},
            "label_schema": CATEGORICAL_SCHEMA,
            "count": 1,
        },
    )
    assert resp.status_code == 422, resp.text


# -- Generation settings are stored and reusable -----------------------------


@pytest.mark.anyio
async def test_generate_stores_its_settings(client: httpx.AsyncClient, monkeypatch) -> None:
    _install_recording_generate(monkeypatch, [])

    created = await client.post(
        "/api/datasets/generate",
        json={
            "name": "gen",
            "description": "some data",
            "instructions": "be subtle",
            "columns": ["question"],
            "column_notes": {"question": "a support ticket"},
            "label_schema": CATEGORICAL_SCHEMA,
            "label_mix": {"good": 0.5, "bad": 0.5},
            "count": 4,
        },
    )
    assert created.status_code == 200, created.text
    ds_id = created.json()["dataset"]["id"]

    resp = await client.get(f"/api/datasets/{ds_id}/generation")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["instructions"] == "be subtle"
    assert body["column_notes"] == {"question": "a support ticket"}
    assert body["label_mix"] == {"good": 0.5, "bad": 0.5}
    assert body["count"] == 4
    # Nothing seeded this dataset, so there is no source version to point back at.
    assert body["source_version_id"] is None


@pytest.mark.anyio
async def test_generate_from_version_stores_its_settings(
    client: httpx.AsyncClient, store: Store, monkeypatch
) -> None:
    version = _make_version(store)
    _install_recording_generate(monkeypatch, [])

    created = await client.post(
        "/api/datasets/generate-from-version",
        json={
            "version_id": version.id,
            "name": "seeded",
            "instructions": "vary the phrasing",
            "include_labels": True,
            "label_guidance": "partial compliance is bad",
            "count": 3,
        },
    )
    assert created.status_code == 200, created.text
    ds_id = created.json()["dataset"]["id"]

    body = (await client.get(f"/api/datasets/{ds_id}/generation")).json()
    assert body["instructions"] == "vary the phrasing"
    assert body["label_guidance"] == "partial compliance is bad"
    assert body["include_labels"] is True
    # Provenance: which version's shape this dataset was built from.
    assert body["source_version_id"] == version.id


@pytest.mark.anyio
async def test_generation_is_null_for_an_uploaded_dataset(client: httpx.AsyncClient) -> None:
    csv = b"question,answer\nq,a\n"
    created = await client.post(
        "/api/datasets/upload",
        files={"file": ("data.csv", csv, "text/csv")},
        data={"name": "uploaded"},
    )
    ds_id = created.json()["dataset"]["id"]

    resp = await client.get(f"/api/datasets/{ds_id}/generation")
    assert resp.status_code == 200, resp.text
    assert resp.json() is None


@pytest.mark.anyio
async def test_generation_for_missing_dataset_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/datasets/nope/generation")
    assert resp.status_code == 404, resp.text


# -- Generating more rows into an existing dataset ---------------------------


@pytest.mark.anyio
async def test_generate_rows_falls_back_to_the_stored_settings(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    calls: list[dict] = []
    _install_recording_generate(monkeypatch, calls)

    created = await client.post(
        "/api/datasets/generate",
        json={
            "name": "gen",
            "description": "some data",
            "instructions": "be subtle",
            "columns": ["question"],
            "column_notes": {"question": "a support ticket"},
            "label_schema": CATEGORICAL_SCHEMA,
            "label_mix": {"good": 0.5, "bad": 0.5},
            "count": 2,
        },
    )
    ds_id = created.json()["dataset"]["id"]

    resp = await client.post(f"/api/datasets/{ds_id}/generate-rows", json={"count": 2})
    assert resp.status_code == 200, resp.text

    # Only `count` was sent, so every steer came from the stored settings.
    top_up = calls[-1]
    assert top_up["description"] == "be subtle"
    assert top_up["column_notes"] == {"question": "a support ticket"}
    assert top_up["label_mix"] == {"good": 0.5, "bad": 0.5}
    assert top_up["count"] == 2


@pytest.mark.anyio
async def test_generate_rows_appends_after_the_existing_rows(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    _install_recording_generate(monkeypatch, [])

    created = await client.post(
        "/api/datasets/generate",
        json={
            "name": "gen",
            "description": "d",
            "columns": ["question"],
            "label_schema": CATEGORICAL_SCHEMA,
            "count": 2,
        },
    )
    ds_id = created.json()["dataset"]["id"]

    appended = (await client.post(f"/api/datasets/{ds_id}/generate-rows", json={"count": 3})).json()
    assert len(appended) == 3
    # Indices continue from the rows already there rather than restarting.
    assert [row["idx"] for row in appended] == [2, 3, 4]

    rows = (await client.get(f"/api/datasets/{ds_id}/rows")).json()
    assert rows["total"] == 5
    for row in rows["rows"]:
        assert row["label_source"] == LabelSource.GENERATED.value


@pytest.mark.anyio
async def test_generate_rows_overrides_win_over_stored_settings(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    calls: list[dict] = []
    _install_recording_generate(monkeypatch, calls)

    created = await client.post(
        "/api/datasets/generate",
        json={
            "name": "gen",
            "description": "d",
            "instructions": "original",
            "columns": ["question"],
            "label_schema": CATEGORICAL_SCHEMA,
            "count": 1,
        },
    )
    ds_id = created.json()["dataset"]["id"]

    await client.post(
        f"/api/datasets/{ds_id}/generate-rows",
        json={"count": 1, "instructions": "sharper"},
    )
    assert calls[-1]["description"] == "sharper"

    # The override becomes the new stored ask, so the next top-up repeats what ran.
    body = (await client.get(f"/api/datasets/{ds_id}/generation")).json()
    assert body["instructions"] == "sharper"


@pytest.mark.anyio
async def test_generate_rows_uses_the_datasets_own_shape(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    # Shape is not overridable: new rows must stay compatible with the existing ones.
    calls: list[dict] = []
    _install_recording_generate(monkeypatch, calls)

    created = await client.post(
        "/api/datasets/generate",
        json={
            "name": "gen",
            "description": "d",
            "columns": ["question", "answer"],
            "label_schema": CATEGORICAL_SCHEMA,
            "count": 1,
        },
    )
    ds_id = created.json()["dataset"]["id"]

    await client.post(f"/api/datasets/{ds_id}/generate-rows", json={"count": 1})

    assert calls[-1]["columns"] == ["question", "answer"]
    assert calls[-1]["label_schema"].kind is ScoreKind.CATEGORICAL


@pytest.mark.anyio
async def test_generate_rows_works_for_an_uploaded_dataset(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    # No stored settings, so the dataset's description is the prompt and nothing else steers.
    calls: list[dict] = []
    _install_recording_generate(monkeypatch, calls)

    csv = b"question\nq\n"
    created = await client.post(
        "/api/datasets/upload",
        files={"file": ("data.csv", csv, "text/csv")},
        data={"name": "uploaded"},
    )
    ds_id = created.json()["dataset"]["id"]

    resp = await client.post(f"/api/datasets/{ds_id}/generate-rows", json={"count": 2})
    assert resp.status_code == 200, resp.text
    assert calls[-1]["column_notes"] is None
    assert calls[-1]["label_mix"] is None


@pytest.mark.anyio
async def test_generate_rows_without_a_label_space_leaves_labels_unset(
    client: httpx.AsyncClient, store: Store, monkeypatch
) -> None:
    version = _make_version(store)
    _install_recording_generate(monkeypatch, [])

    created = await client.post(
        "/api/datasets/generate-from-version",
        json={
            "version_id": version.id,
            "name": "no-labels",
            "include_labels": False,
            "count": 1,
        },
    )
    ds_id = created.json()["dataset"]["id"]

    appended = (await client.post(f"/api/datasets/{ds_id}/generate-rows", json={"count": 1})).json()
    assert appended[0]["suggested_label"] is None


@pytest.mark.anyio
async def test_generate_rows_label_guidance_without_a_label_space_is_422(
    client: httpx.AsyncClient, store: Store, monkeypatch
) -> None:
    version = _make_version(store)
    _install_recording_generate(monkeypatch, [])

    created = await client.post(
        "/api/datasets/generate-from-version",
        json={
            "version_id": version.id,
            "name": "no-labels",
            "include_labels": False,
            "count": 1,
        },
    )
    ds_id = created.json()["dataset"]["id"]

    resp = await client.post(
        f"/api/datasets/{ds_id}/generate-rows",
        json={"count": 1, "label_guidance": "partial is bad"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.anyio
async def test_generate_rows_unknown_column_note_key_is_422(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    _install_recording_generate(monkeypatch, [])

    created = await client.post(
        "/api/datasets/generate",
        json={
            "name": "gen",
            "description": "d",
            "columns": ["question"],
            "label_schema": CATEGORICAL_SCHEMA,
            "count": 1,
        },
    )
    ds_id = created.json()["dataset"]["id"]

    resp = await client.post(
        f"/api/datasets/{ds_id}/generate-rows",
        json={"count": 1, "column_notes": {"quesiton": "oops"}},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.anyio
async def test_generate_rows_count_over_cap_is_422(client: httpx.AsyncClient, monkeypatch) -> None:
    _install_recording_generate(monkeypatch, [])

    created = await client.post(
        "/api/datasets/generate",
        json={
            "name": "gen",
            "description": "d",
            "columns": ["question"],
            "label_schema": CATEGORICAL_SCHEMA,
            "count": 1,
        },
    )
    ds_id = created.json()["dataset"]["id"]

    resp = await client.post(f"/api/datasets/{ds_id}/generate-rows", json={"count": 201})
    assert resp.status_code == 422, resp.text


@pytest.mark.anyio
async def test_generate_rows_for_missing_dataset_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/datasets/nope/generate-rows", json={"count": 1})
    assert resp.status_code == 404, resp.text
