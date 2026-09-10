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
    resp = await client.post(
        "/api/datasets",
        json={
            "name": "ds",
            "columns": ["q"],
            "label_schema": {"kind": "categorical", "labels": ["a"]},
        },
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
