"""Tests for the read-only setup endpoint: key presence, envelope shape, and secret hygiene.

``GET /api/setup`` is the only surface for reporting configuration status; keys are written
only via the CLI, so there is no POST here. Presence must reflect the *effective* value (an
exported env var counts, matching ``apply_gateway_key``'s env-wins precedence), and the response
must never carry a key's actual value -- only booleans -- so a future field addition that leaked
one would be caught here rather than in production.
"""

from collections.abc import AsyncIterator

import httpx
import pytest

from valcore.api.deps import get_store
from valcore.api.main import create_app
from valcore.config import FileConfig, save_config
from valcore.store import Store, create_engine, init_db

GATEWAY_ENV = "PYDANTIC_AI_GATEWAY_API_KEY"
LOGFIRE_TOKEN_ENV = "LOGFIRE_TOKEN"

CATEGORICAL_SCHEMA = {"kind": "categorical", "labels": ["good", "bad"]}


@pytest.fixture(autouse=True)
def _no_ambient_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with neither env var set, so presence reflects only what the test sets."""
    monkeypatch.delenv(GATEWAY_ENV, raising=False)
    monkeypatch.delenv(LOGFIRE_TOKEN_ENV, raising=False)


def _client(app) -> httpx.AsyncClient:
    """Return an ASGI-backed client bound to the given app."""
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _get_setup(app) -> dict:
    async with _client(app) as client:
        resp = await client.get("/api/setup")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _by_name(body: dict) -> dict[str, dict]:
    return {entry["name"]: entry for entry in body["keys"]}


# -- Envelope shape -------------------------------------------------------------


@pytest.mark.anyio
async def test_setup_lists_exactly_the_three_documented_keys() -> None:
    body = await _get_setup(create_app())
    assert {entry["name"] for entry in body["keys"]} == {
        "gateway_api_key",
        "logfire_token",
        "logfire_api_key",
    }


@pytest.mark.anyio
async def test_gateway_key_metadata_matches_the_documented_contract() -> None:
    body = await _get_setup(create_app())
    entry = _by_name(body)["gateway_api_key"]
    assert entry["required"] is True
    assert entry["label"] == "Pydantic AI Gateway key"
    assert entry["command"] == "valcore config set-key"
    assert entry["purpose"] == "Runs evaluators and generates evaluators and datasets."


@pytest.mark.anyio
async def test_logfire_token_metadata_matches_the_documented_contract() -> None:
    body = await _get_setup(create_app())
    entry = _by_name(body)["logfire_token"]
    assert entry["required"] is False
    assert entry["label"] == "Logfire write token"
    assert entry["command"] == "valcore config set-logfire-token"
    assert entry["purpose"] == "Sends run traces to Logfire."


@pytest.mark.anyio
async def test_logfire_api_key_metadata_matches_the_documented_contract() -> None:
    body = await _get_setup(create_app())
    entry = _by_name(body)["logfire_api_key"]
    assert entry["required"] is False
    assert entry["label"] == "Logfire API key"
    assert entry["command"] == "valcore config set-logfire-key"
    assert entry["purpose"] == "Pushes datasets to Logfire's hosted store."


# -- Effective presence: gateway_api_key (env + file, four cases) --------------


@pytest.mark.anyio
async def test_gateway_key_absent_from_neither() -> None:
    body = await _get_setup(create_app())
    assert _by_name(body)["gateway_api_key"]["set"] is False


@pytest.mark.anyio
async def test_gateway_key_present_from_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GATEWAY_ENV, "sk-from-env")
    body = await _get_setup(create_app())
    assert _by_name(body)["gateway_api_key"]["set"] is True


@pytest.mark.anyio
async def test_gateway_key_present_from_file_only() -> None:
    save_config(FileConfig(gateway_api_key="sk-from-file"))
    body = await _get_setup(create_app())
    assert _by_name(body)["gateway_api_key"]["set"] is True


@pytest.mark.anyio
async def test_gateway_key_present_from_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GATEWAY_ENV, "sk-from-env")
    save_config(FileConfig(gateway_api_key="sk-from-file"))
    body = await _get_setup(create_app())
    assert _by_name(body)["gateway_api_key"]["set"] is True


# -- Effective presence: logfire_token (env + file, four cases) ----------------


@pytest.mark.anyio
async def test_logfire_token_absent_from_neither() -> None:
    body = await _get_setup(create_app())
    assert _by_name(body)["logfire_token"]["set"] is False


@pytest.mark.anyio
async def test_logfire_token_present_from_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LOGFIRE_TOKEN_ENV, "lf-from-env")
    body = await _get_setup(create_app())
    assert _by_name(body)["logfire_token"]["set"] is True


@pytest.mark.anyio
async def test_logfire_token_present_from_file_only() -> None:
    save_config(FileConfig(logfire_token="lf-from-file"))
    body = await _get_setup(create_app())
    assert _by_name(body)["logfire_token"]["set"] is True


@pytest.mark.anyio
async def test_logfire_token_present_from_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LOGFIRE_TOKEN_ENV, "lf-from-env")
    save_config(FileConfig(logfire_token="lf-from-file"))
    body = await _get_setup(create_app())
    assert _by_name(body)["logfire_token"]["set"] is True


# -- Effective presence: logfire_api_key (file-only; env never counts) --------


@pytest.mark.anyio
async def test_logfire_api_key_absent_by_default() -> None:
    body = await _get_setup(create_app())
    assert _by_name(body)["logfire_api_key"]["set"] is False


@pytest.mark.anyio
async def test_logfire_api_key_present_from_file() -> None:
    save_config(FileConfig(logfire_api_key="lf-api-key-from-file"))
    body = await _get_setup(create_app())
    assert _by_name(body)["logfire_api_key"]["set"] is True


@pytest.mark.anyio
async def test_logfire_api_key_ignores_a_same_named_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """There is no env var for this key; even one with a plausible name must not count."""
    monkeypatch.setenv("LOGFIRE_API_KEY", "lf-from-env")
    body = await _get_setup(create_app())
    assert _by_name(body)["logfire_api_key"]["set"] is False


@pytest.mark.anyio
async def test_logfire_api_key_present_from_both_file_and_env_lookalike(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOGFIRE_API_KEY", "lf-from-env")
    save_config(FileConfig(logfire_api_key="lf-api-key-from-file"))
    body = await _get_setup(create_app())
    assert _by_name(body)["logfire_api_key"]["set"] is True


# -- No key value ever appears in the response ---------------------------------


@pytest.mark.anyio
async def test_no_secret_value_leaks_into_the_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression guard against a future field leaking a configured secret."""
    monkeypatch.setenv(GATEWAY_ENV, "sk-super-secret-gateway-value")
    save_config(
        FileConfig(
            logfire_token="lf-super-secret-token-value",
            logfire_api_key="lf-super-secret-apikey-value",
        )
    )
    async with _client(create_app()) as client:
        resp = await client.get("/api/setup")
    assert resp.status_code == 200, resp.text
    raw = resp.text
    for secret in (
        "sk-super-secret-gateway-value",
        "lf-super-secret-token-value",
        "lf-super-secret-apikey-value",
    ):
        assert secret not in raw


# -- No POST route --------------------------------------------------------------


@pytest.mark.anyio
async def test_post_setup_is_not_allowed() -> None:
    async with _client(create_app()) as client:
        resp = await client.post("/api/setup", json={})
    assert resp.status_code == 405


# -- App starts and serves health with no Logfire token configured -------------


@pytest.mark.anyio
async def test_health_still_works_with_no_logfire_token_configured() -> None:
    async with _client(create_app()) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_create_app_is_idempotent_across_repeated_calls() -> None:
    """configure_tracing/instrument_fastapi must tolerate create_app() running more than once."""
    create_app()
    app_again = create_app()
    async with _client(app_again) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200


# -- Ungated endpoints keep working with no gateway key ------------------------


@pytest.fixture
def store(tmp_path) -> Store:
    """A fresh file-backed store isolated per test."""
    engine = create_engine(tmp_path / "setup.db")
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


@pytest.mark.anyio
async def test_ungated_endpoints_still_work_with_no_gateway_key(
    client: httpx.AsyncClient,
) -> None:
    """Manual authoring, upload, labeling, and export must never require the gateway key."""
    created = await client.post(
        "/api/datasets",
        json={
            "name": "blank",
            "description": "",
            "columns": ["question"],
            "label_schema": CATEGORICAL_SCHEMA,
        },
    )
    assert created.status_code == 200, created.text
    ds_id = created.json()["id"]

    csv = b"question,answer\nq1,a1\n"
    uploaded = await client.post(
        "/api/datasets/upload",
        files={"file": ("d.csv", csv, "text/csv")},
        data={"name": "uploaded"},
    )
    assert uploaded.status_code == 200, uploaded.text

    appended = await client.post(f"/api/datasets/{ds_id}/rows", json={"rows": [{"question": "q"}]})
    assert appended.status_code == 200, appended.text
    row_id = appended.json()[0]["id"]

    patched = await client.patch(f"/api/datasets/rows/{row_id}", json={"label": "good"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["label"] == {"value": "good"}

    eval_created = await client.post("/api/evaluators", json={"name": "E"})
    assert eval_created.status_code == 200, eval_created.text
    eval_id = eval_created.json()["id"]

    version = await client.post(
        f"/api/evaluators/{eval_id}/versions",
        json={
            "version_name": "v1",
            "notes": "",
            "model": "gateway/anthropic:claude-sonnet-5",
            "instructions": "Judge.",
            "prompt_template": "Input: {question}",
            "required_columns": ["question"],
            "output_fields": [
                {
                    "name": "verdict",
                    "type": "enum",
                    "description": "v",
                    "enum_values": ["good", "bad"],
                }
            ],
            "score_field": "verdict",
            "score_kind": "categorical",
            "score_labels": ["good", "bad"],
        },
    )
    assert version.status_code == 200, version.text

    exported_version = await client.get(f"/api/evaluators/versions/{version.json()['id']}/export")
    assert exported_version.status_code == 200, exported_version.text

    exported_dataset = await client.get(f"/api/datasets/{ds_id}/export.json")
    assert exported_dataset.status_code == 200, exported_dataset.text
