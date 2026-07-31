"""Tests for the FastAPI skeleton: health, config, error mapping, and the EventBus."""

import asyncio

import httpx
import pytest

from valcore.api.events import EventBus
from valcore.api.main import create_app
from valcore.errors import (
    ConfigError,
    ContractError,
    FrozenVersionError,
    NotFoundError,
    ValcoreError,
)


def _client(app) -> httpx.AsyncClient:
    """Return an ASGI-backed client bound to the given app."""
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.anyio
async def test_health() -> None:
    app = create_app()
    async with _client(app) as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_config_populated() -> None:
    app = create_app()
    async with _client(app) as client:
        response = await client.get("/api/config")
    assert response.status_code == 200
    payload = response.json()
    assert payload["models"]
    assert payload["tools"]
    assert payload["capabilities"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "status", "type_name"),
    [
        (NotFoundError("missing"), 404, "NotFoundError"),
        (ContractError("mismatch"), 422, "ContractError"),
        (ConfigError("bad config"), 422, "ConfigError"),
        (FrozenVersionError("frozen"), 409, "FrozenVersionError"),
        (ValcoreError("generic"), 400, "ValcoreError"),
    ],
)
async def test_exception_mapping(error: ValcoreError, status: int, type_name: str) -> None:
    app = create_app()

    @app.get("/boom")
    async def boom() -> None:
        raise error

    async with _client(app) as client:
        response = await client.get("/boom")
    assert response.status_code == status
    assert response.json() == {"error": {"type": type_name, "message": str(error)}}


@pytest.mark.anyio
async def test_app_starts_without_spa_dist() -> None:
    app = create_app()
    routes = {getattr(route, "path", None) for route in app.routes}
    assert "/api/health" in routes
    async with _client(app) as client:
        response = await client.get("/api/health")
    assert response.status_code == 200


async def _prime(bus: EventBus, run_id: str):
    """Start a subscription and return its generator plus its first pending event future."""
    gen = bus.subscribe(run_id)
    pending = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0)
    return gen, pending


@pytest.mark.anyio
async def test_eventbus_delivers_to_subscriber() -> None:
    bus = EventBus()
    gen, pending = await _prime(bus, "run-1")
    bus.publish("run-1", {"type": "started"})
    event = await pending
    assert event == {"type": "started"}
    await gen.aclose()


@pytest.mark.anyio
async def test_eventbus_supports_two_independent_subscribers() -> None:
    bus = EventBus()
    gen_a, pending_a = await _prime(bus, "run-1")
    gen_b, pending_b = await _prime(bus, "run-1")

    bus.publish("run-1", {"type": "started", "n": 1})

    assert await pending_a == {"type": "started", "n": 1}
    assert await pending_b == {"type": "started", "n": 1}

    await gen_a.aclose()
    await gen_b.aclose()
