"""Tests for the annotations API router: label set CRUD and per-row annotations."""

from collections.abc import AsyncIterator

import httpx
import pytest

from valcore.api.deps import get_store
from valcore.api.main import create_app
from valcore.store import Store, create_engine, init_db


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


async def _make_dataset(client: httpx.AsyncClient) -> str:
    # No label_schema: these tests create their own label sets explicitly, and a
    # label_schema here would auto-create an extra "Labels" label set alongside them.
    resp = await client.post(
        "/api/datasets",
        json={"name": "ds", "columns": ["q"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


# -- Label sets ----------------------------------------------------------------


@pytest.mark.anyio
async def test_create_and_get_label_set(client: httpx.AsyncClient) -> None:
    dataset_id = await _make_dataset(client)
    resp = await client.post(
        f"/api/datasets/{dataset_id}/label-sets",
        json={
            "name": "quality",
            "description": "human review",
            "kind": "categorical",
            "labels": [{"name": "good", "description": "meets the bar"}],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "quality"
    assert body["kind"] == "categorical"
    assert body["labels"] == [{"name": "good", "description": "meets the bar"}]

    fetched = await client.get(f"/api/label-sets/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]


@pytest.mark.anyio
async def test_create_label_set_invalid_shape_is_422(client: httpx.AsyncClient) -> None:
    dataset_id = await _make_dataset(client)
    resp = await client.post(
        f"/api/datasets/{dataset_id}/label-sets",
        json={"name": "bad", "description": "", "kind": "categorical", "labels": None},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_list_label_sets_includes_progress(client: httpx.AsyncClient) -> None:
    dataset_id = await _make_dataset(client)
    await client.post(
        f"/api/datasets/{dataset_id}/label-sets",
        json={
            "name": "quality",
            "description": "",
            "kind": "categorical",
            "labels": [{"name": "good", "description": "d"}],
        },
    )
    resp = await client.get(f"/api/datasets/{dataset_id}/label-sets")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["annotated_count"] == 0
    assert body[0]["row_count"] == 0


@pytest.mark.anyio
async def test_update_label_set_renames(client: httpx.AsyncClient) -> None:
    dataset_id = await _make_dataset(client)
    created = (
        await client.post(
            f"/api/datasets/{dataset_id}/label-sets",
            json={
                "name": "quality",
                "description": "",
                "kind": "categorical",
                "labels": [{"name": "good", "description": "d"}],
            },
        )
    ).json()
    resp = await client.patch(f"/api/label-sets/{created['id']}", json={"name": "renamed"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "renamed"


@pytest.mark.anyio
async def test_delete_label_set(client: httpx.AsyncClient) -> None:
    dataset_id = await _make_dataset(client)
    created = (
        await client.post(
            f"/api/datasets/{dataset_id}/label-sets",
            json={
                "name": "quality",
                "description": "",
                "kind": "categorical",
                "labels": [{"name": "good", "description": "d"}],
            },
        )
    ).json()
    resp = await client.delete(f"/api/label-sets/{created['id']}")
    assert resp.status_code == 204
    assert (await client.get(f"/api/label-sets/{created['id']}")).status_code == 404


@pytest.mark.anyio
async def test_get_unknown_label_set_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/label-sets/does-not-exist")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_list_label_sets_unknown_dataset_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/datasets/does-not-exist/label-sets")
    assert resp.status_code == 404


# -- Annotation rows -------------------------------------------------------


async def _make_label_set(client: httpx.AsyncClient, dataset_id: str) -> dict:
    resp = await client.post(
        f"/api/datasets/{dataset_id}/label-sets",
        json={
            "name": "quality",
            "description": "",
            "kind": "categorical",
            "labels": [{"name": "good", "description": "d"}, {"name": "bad", "description": "d"}],
        },
    )
    return resp.json()


@pytest.mark.anyio
async def test_rows_page_lists_rows_with_no_annotation(client: httpx.AsyncClient) -> None:
    dataset_id = await _make_dataset(client)
    await client.post(f"/api/datasets/{dataset_id}/rows", json={"rows": [{"q": "1"}, {"q": "2"}]})
    label_set = await _make_label_set(client, dataset_id)

    resp = await client.get(f"/api/label-sets/{label_set['id']}/rows")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert body["annotated_count"] == 0
    assert all(row["annotation"] is None for row in body["rows"])


@pytest.mark.anyio
async def test_put_annotation_creates_and_reflects_in_rows_page(client: httpx.AsyncClient) -> None:
    dataset_id = await _make_dataset(client)
    rows_resp = await client.post(f"/api/datasets/{dataset_id}/rows", json={"rows": [{"q": "1"}]})
    row_id = rows_resp.json()[0]["id"]
    label_set = await _make_label_set(client, dataset_id)

    put_resp = await client.put(
        f"/api/label-sets/{label_set['id']}/rows/{row_id}/annotation",
        json={"labels": ["good"], "description": "looks right"},
    )
    assert put_resp.status_code == 200, put_resp.text
    body = put_resp.json()
    assert body["labels"] == ["good"]
    assert body["description"] == "looks right"
    assert body["source"] == "manual"

    page = (await client.get(f"/api/label-sets/{label_set['id']}/rows")).json()
    assert page["annotated_count"] == 1
    assert page["rows"][0]["annotation"]["labels"] == ["good"]


@pytest.mark.anyio
async def test_put_annotation_rejects_unknown_label(client: httpx.AsyncClient) -> None:
    dataset_id = await _make_dataset(client)
    rows_resp = await client.post(f"/api/datasets/{dataset_id}/rows", json={"rows": [{"q": "1"}]})
    row_id = rows_resp.json()[0]["id"]
    label_set = await _make_label_set(client, dataset_id)

    resp = await client.put(
        f"/api/label-sets/{label_set['id']}/rows/{row_id}/annotation",
        json={"labels": ["unknown"]},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_get_annotation_returns_null_when_unset(client: httpx.AsyncClient) -> None:
    dataset_id = await _make_dataset(client)
    rows_resp = await client.post(f"/api/datasets/{dataset_id}/rows", json={"rows": [{"q": "1"}]})
    row_id = rows_resp.json()[0]["id"]
    label_set = await _make_label_set(client, dataset_id)

    resp = await client.get(f"/api/label-sets/{label_set['id']}/rows/{row_id}/annotation")
    assert resp.status_code == 200
    assert resp.json() is None


@pytest.mark.anyio
async def test_delete_annotation_clears_it(client: httpx.AsyncClient) -> None:
    dataset_id = await _make_dataset(client)
    rows_resp = await client.post(f"/api/datasets/{dataset_id}/rows", json={"rows": [{"q": "1"}]})
    row_id = rows_resp.json()[0]["id"]
    label_set = await _make_label_set(client, dataset_id)
    await client.put(
        f"/api/label-sets/{label_set['id']}/rows/{row_id}/annotation", json={"labels": ["good"]}
    )

    resp = await client.delete(f"/api/label-sets/{label_set['id']}/rows/{row_id}/annotation")
    assert resp.status_code == 204
    assert (
        await client.get(f"/api/label-sets/{label_set['id']}/rows/{row_id}/annotation")
    ).json() is None


@pytest.mark.anyio
async def test_get_annotation_wrong_dataset_row_is_422(client: httpx.AsyncClient) -> None:
    # Create two datasets
    dataset_a_id = await _make_dataset(client)
    dataset_b_id = await _make_dataset(client)

    # Create label set on dataset A
    label_set = await _make_label_set(client, dataset_a_id)

    # Create row on dataset B
    rows_resp = await client.post(f"/api/datasets/{dataset_b_id}/rows", json={"rows": [{"q": "1"}]})
    row_b_id = rows_resp.json()[0]["id"]

    # Try to get annotation for row from dataset B with label set from dataset A
    resp = await client.get(f"/api/label-sets/{label_set['id']}/rows/{row_b_id}/annotation")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_put_annotation_wrong_dataset_row_is_422(client: httpx.AsyncClient) -> None:
    # Create two datasets
    dataset_a_id = await _make_dataset(client)
    dataset_b_id = await _make_dataset(client)

    # Create label set on dataset A
    label_set = await _make_label_set(client, dataset_a_id)

    # Create row on dataset B
    rows_resp = await client.post(f"/api/datasets/{dataset_b_id}/rows", json={"rows": [{"q": "1"}]})
    row_b_id = rows_resp.json()[0]["id"]

    # Try to put annotation for row from dataset B with label set from dataset A
    resp = await client.put(
        f"/api/label-sets/{label_set['id']}/rows/{row_b_id}/annotation",
        json={"labels": ["good"]},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_delete_annotation_wrong_dataset_row_is_422(client: httpx.AsyncClient) -> None:
    # Create two datasets
    dataset_a_id = await _make_dataset(client)
    dataset_b_id = await _make_dataset(client)

    # Create label set on dataset A
    label_set = await _make_label_set(client, dataset_a_id)

    # Create row on dataset B
    rows_resp = await client.post(f"/api/datasets/{dataset_b_id}/rows", json={"rows": [{"q": "1"}]})
    row_b_id = rows_resp.json()[0]["id"]

    # Try to delete annotation for row from dataset B with label set from dataset A
    resp = await client.delete(f"/api/label-sets/{label_set['id']}/rows/{row_b_id}/annotation")
    assert resp.status_code == 422


# -- Accept annotation suggestion -------------------------------------------------------


@pytest.mark.anyio
async def test_accept_annotation_promotes_suggestion(
    client: httpx.AsyncClient, store: Store
) -> None:
    from valcore.models import LabelSource

    dataset_id = await _make_dataset(client)
    rows_resp = await client.post(f"/api/datasets/{dataset_id}/rows", json={"rows": [{"q": "1"}]})
    row_id = rows_resp.json()[0]["id"]
    label_set = await _make_label_set(client, dataset_id)
    store.set_annotation(
        label_set["id"], row_id, suggested_labels=["good"], source=LabelSource.GENERATED
    )

    resp = await client.post(f"/api/label-sets/{label_set['id']}/rows/{row_id}/annotation/accept")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["labels"] == ["good"]
    assert body["source"] == "accepted"


@pytest.mark.anyio
async def test_accept_annotation_without_suggestion_is_422(client: httpx.AsyncClient) -> None:
    dataset_id = await _make_dataset(client)
    rows_resp = await client.post(f"/api/datasets/{dataset_id}/rows", json={"rows": [{"q": "1"}]})
    row_id = rows_resp.json()[0]["id"]
    label_set = await _make_label_set(client, dataset_id)

    resp = await client.post(f"/api/label-sets/{label_set['id']}/rows/{row_id}/annotation/accept")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_accept_annotation_wrong_dataset_row_is_422(
    client: httpx.AsyncClient, store: Store
) -> None:

    dataset_a_id = await _make_dataset(client)
    dataset_b_id = await _make_dataset(client)
    label_set = await _make_label_set(client, dataset_a_id)
    row_b = store.add_rows(dataset_b_id, [{"q": "1"}])[0]

    resp = await client.post(f"/api/label-sets/{label_set['id']}/rows/{row_b.id}/annotation/accept")
    assert resp.status_code == 422
