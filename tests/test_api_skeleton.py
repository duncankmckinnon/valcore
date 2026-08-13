"""Tests for the FastAPI skeleton: health, config, error mapping, and the EventBus."""

import asyncio

import httpx
import pytest

from valcore.api import main
from valcore.api.events import EventBus
from valcore.api.main import create_app
from valcore.errors import (
    ConfigError,
    ContractError,
    DestructiveChangeError,
    FrozenVersionError,
    NotFoundError,
    ReferencedError,
    ValcoreError,
)
from valcore.settings import get_settings


@pytest.fixture
def no_spa(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build apps with no SPA mounted, regardless of whether web/dist was built.

    ``create_app`` mounts ``StaticFiles`` at ``/`` when the SPA exists, and that
    catch-all shadows any route registered after the app is built. Tests that
    attach a route post-hoc must pin this off, or they pass only on machines
    where ``npm run build`` has never run.
    """
    monkeypatch.setattr(main, "_resolve_dist_dir", lambda: None)


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
async def test_config_default_model_is_not_merely_the_head_of_the_catalog() -> None:
    """The SPA seeds new versions from ``default_model``, never ``models[0]``.

    The catalog is sorted alphabetically, so its first entry is an arbitrary Bedrock
    model. This pins the two apart so a regression to ``models[0]`` is caught here
    rather than by a user wondering why their evaluator picked a model they never chose.
    """
    app = create_app()
    async with _client(app) as client:
        payload = (await client.get("/api/config")).json()

    assert payload["default_model"] == get_settings().default_model
    assert payload["default_model"] in payload["models"]
    assert payload["default_model"] != payload["models"][0]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "status", "type_name"),
    [
        (NotFoundError("missing"), 404, "NotFoundError"),
        (ContractError("mismatch"), 422, "ContractError"),
        (ConfigError("bad config"), 422, "ConfigError"),
        (FrozenVersionError("frozen"), 409, "FrozenVersionError"),
        (ReferencedError("referenced"), 409, "ReferencedError"),
        (DestructiveChangeError("destructive"), 409, "DestructiveChangeError"),
        (ValcoreError("generic"), 400, "ValcoreError"),
    ],
)
async def test_exception_mapping(
    error: ValcoreError, status: int, type_name: str, no_spa: None
) -> None:
    app = create_app()

    @app.get("/boom")
    async def boom() -> None:
        raise error

    async with _client(app) as client:
        response = await client.get("/boom")
    assert response.status_code == status
    assert response.json() == {"error": {"type": type_name, "message": str(error)}}


@pytest.mark.anyio
async def test_error_response_includes_detail_payload(no_spa: None) -> None:
    app = create_app()

    @app.get("/boom")
    async def boom() -> None:
        raise ReferencedError("still referenced", detail={"run_count": 4})

    async with _client(app) as client:
        response = await client.get("/boom")
    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "type": "ReferencedError",
            "message": "still referenced",
            "detail": {"run_count": 4},
        }
    }


@pytest.mark.anyio
async def test_error_without_detail_omits_detail_field(no_spa: None) -> None:
    app = create_app()

    @app.get("/boom")
    async def boom() -> None:
        raise ConfigError("bad config")

    async with _client(app) as client:
        response = await client.get("/boom")
    assert response.status_code == 422
    # Existing envelope stays byte-identical: no null or empty detail leaks in.
    assert response.json() == {"error": {"type": "ConfigError", "message": "bad config"}}


@pytest.mark.anyio
async def test_detail_with_zero_count_is_still_emitted(no_spa: None) -> None:
    app = create_app()

    @app.get("/boom")
    async def boom() -> None:
        raise ReferencedError("referenced", detail={"run_count": 0})

    async with _client(app) as client:
        response = await client.get("/boom")
    assert response.status_code == 409
    # A non-empty detail must survive the ``or None`` guard even when its values are falsy.
    assert response.json() == {
        "error": {
            "type": "ReferencedError",
            "message": "referenced",
            "detail": {"run_count": 0},
        }
    }


def test_valcore_error_positional_message_still_works() -> None:
    assert str(ConfigError("boom")) == "boom"


def test_valcore_error_detail_defaults_to_empty_dict() -> None:
    assert ConfigError("boom").detail == {}


def test_valcore_error_carries_detail_payload() -> None:
    error = ReferencedError("still referenced", detail={"run_count": 4})
    assert error.detail == {"run_count": 4}
    assert str(error) == "still referenced"


@pytest.mark.anyio
async def test_app_starts_without_spa_dist(no_spa: None) -> None:
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
