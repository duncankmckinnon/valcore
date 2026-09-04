"""Tests for the setup endpoint: key presence, envelope shape, writes, and secret hygiene.

``GET /api/setup`` reports configuration status. ``POST /api/setup`` writes keys to the local
config file. Presence must reflect the *effective* value (an exported env var counts, matching
``apply_gateway_key``'s env-wins precedence), and no response may carry a key's actual value --
only booleans -- so a future field addition that leaked one would be caught here rather than in
production.
"""

from collections.abc import AsyncIterator

import httpx
import pytest

from valcore.api.deps import get_store
from valcore.api.main import create_app
from valcore.config import FileConfig, save_config
from valcore.logfire_links import LogfireLinks
from valcore.store import Store, create_engine, init_db

GATEWAY_ENV = "PYDANTIC_AI_GATEWAY_API_KEY"
LOGFIRE_TOKEN_ENV = "LOGFIRE_TOKEN"

CATEGORICAL_SCHEMA = {"kind": "categorical", "labels": ["good", "bad"]}


@pytest.fixture(autouse=True)
def _no_ambient_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with neither env var set, so presence reflects only what the test sets."""
    monkeypatch.delenv(GATEWAY_ENV, raising=False)
    monkeypatch.delenv(LOGFIRE_TOKEN_ENV, raising=False)


@pytest.fixture(autouse=True)
def _no_logfire_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not call Logfire from setup GET unless a test installs its own resolver."""

    async def _none(_key: str | None = None) -> LogfireLinks | None:
        return None

    monkeypatch.setattr("valcore.logfire_links.resolve_logfire_links", _none)


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
async def test_setup_lists_exactly_the_four_documented_keys() -> None:
    body = await _get_setup(create_app())
    assert [entry["name"] for entry in body["keys"]] == [
        "gateway_api_key",
        "logfire_token",
        "logfire_read_key",
        "logfire_write_key",
    ]


@pytest.mark.anyio
async def test_gateway_key_metadata_matches_the_documented_contract() -> None:
    body = await _get_setup(create_app())
    entry = _by_name(body)["gateway_api_key"]
    assert entry["required"] is True
    assert entry["label"] == "Pydantic AI Gateway key"
    assert entry["command"] == "valcore config set-key"
    assert entry["purpose"] == "Runs evaluators and generates evaluators and datasets."
    assert entry["from_env"] is False
    assert "Pydantic AI Gateway" in entry["explanation"]
    assert "Logfire" in entry["explanation"]


@pytest.mark.anyio
async def test_logfire_token_metadata_matches_the_documented_contract() -> None:
    body = await _get_setup(create_app())
    entry = _by_name(body)["logfire_token"]
    assert entry["required"] is False
    assert entry["label"] == "Logfire tracing token"
    assert entry["command"] == "valcore config set-logfire-token"
    assert entry["purpose"] == (
        "Sends valcore's FastAPI, gateway, and run traces to your valcore Logfire project."
    )
    assert entry["from_env"] is False
    assert "write token" in entry["explanation"].lower()
    assert "different project" in entry["explanation"]


@pytest.mark.anyio
async def test_logfire_read_key_metadata_matches_the_documented_contract() -> None:
    body = await _get_setup(create_app())
    entry = _by_name(body)["logfire_read_key"]
    assert entry["required"] is False
    assert entry["label"] == "Logfire read key"
    assert entry["command"] == "valcore config set-logfire-read-key"
    assert entry["purpose"] == (
        "Queries traces and hosted datasets in the Logfire project you are sampling from."
    )
    assert entry["from_env"] is False
    assert "project:read" in entry["explanation"]
    assert "project:read_datasets" in entry["explanation"]
    assert "sampling from" in entry["explanation"]
    assert "valcore" in entry["explanation"].lower()


@pytest.mark.anyio
async def test_logfire_write_key_metadata_matches_the_documented_contract() -> None:
    body = await _get_setup(create_app())
    entry = _by_name(body)["logfire_write_key"]
    assert entry["required"] is False
    assert entry["label"] == "Logfire write key"
    assert entry["command"] == "valcore config set-logfire-write-key"
    assert entry["purpose"] == "Pushes datasets to your valcore Logfire project."
    assert entry["from_env"] is False
    assert "project:write_datasets" in entry["explanation"]
    assert "valcore" in entry["explanation"].lower()


# -- Effective presence: gateway_api_key (env + file, four cases) --------------


@pytest.mark.anyio
async def test_gateway_key_absent_from_neither() -> None:
    body = await _get_setup(create_app())
    assert _by_name(body)["gateway_api_key"]["set"] is False


@pytest.mark.anyio
async def test_gateway_key_present_from_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GATEWAY_ENV, "sk-from-env")
    body = await _get_setup(create_app())
    entry = _by_name(body)["gateway_api_key"]
    assert entry["set"] is True
    assert entry["from_env"] is True


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
    entry = _by_name(body)["logfire_token"]
    assert entry["set"] is True
    assert entry["from_env"] is True


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


# -- Effective presence: logfire_read_key / logfire_write_key (file-only) ------


@pytest.mark.anyio
async def test_logfire_read_and_write_keys_absent_by_default() -> None:
    body = await _get_setup(create_app())
    names = _by_name(body)
    assert names["logfire_read_key"]["set"] is False
    assert names["logfire_write_key"]["set"] is False


@pytest.mark.anyio
async def test_logfire_read_key_present_from_file() -> None:
    save_config(FileConfig(logfire_read_key="lf-read-from-file"))
    body = await _get_setup(create_app())
    names = _by_name(body)
    assert names["logfire_read_key"]["set"] is True
    assert names["logfire_write_key"]["set"] is False


@pytest.mark.anyio
async def test_logfire_write_key_present_from_file() -> None:
    save_config(FileConfig(logfire_write_key="lf-write-from-file"))
    body = await _get_setup(create_app())
    names = _by_name(body)
    assert names["logfire_write_key"]["set"] is True
    assert names["logfire_read_key"]["set"] is False


@pytest.mark.anyio
async def test_legacy_logfire_api_key_marks_both_read_and_write_set() -> None:
    save_config(FileConfig(logfire_api_key="lf-api-key-from-file"))
    body = await _get_setup(create_app())
    names = _by_name(body)
    assert names["logfire_read_key"]["set"] is True
    assert names["logfire_write_key"]["set"] is True


@pytest.mark.anyio
async def test_logfire_read_key_ignores_a_same_named_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOGFIRE_API_KEY", "lf-from-env")
    body = await _get_setup(create_app())
    assert _by_name(body)["logfire_read_key"]["set"] is False
    assert _by_name(body)["logfire_write_key"]["set"] is False


# -- SQL Workbench URL (not a secret; returned as the value) -------------------


_DERIVED_SOURCE = LogfireLinks(
    explore_url="https://logfire-us.pydantic.dev/duncan/agent-tracing/explore",
    traces_url="https://logfire-us.pydantic.dev/duncan/agent-tracing?last=%2230m%22",
    datasets_url="https://logfire-us.pydantic.dev/duncan/agent-tracing/evals",
)
_DERIVED_VALCORE = LogfireLinks(
    explore_url="https://logfire-us.pydantic.dev/duncan/valcore/explore",
    traces_url="https://logfire-us.pydantic.dev/duncan/valcore?last=%2230m%22",
    datasets_url="https://logfire-us.pydantic.dev/duncan/valcore/evals",
)


@pytest.mark.anyio
async def test_setup_returns_explore_url_when_configured() -> None:
    save_config(
        FileConfig(
            logfire_explore_url="https://logfire-us.pydantic.dev/duncan/agent-tracing/explore"
        )
    )
    body = await _get_setup(create_app())
    assert body["logfire_explore_url"] == (
        "https://logfire-us.pydantic.dev/duncan/agent-tracing/explore"
    )


@pytest.mark.anyio
async def test_setup_explore_url_is_null_by_default() -> None:
    body = await _get_setup(create_app())
    assert body["logfire_explore_url"] is None
    assert body["logfire_traces_url"] is None
    assert body["logfire_datasets_url"] is None


@pytest.mark.anyio
async def test_setup_prefers_derived_project_links_over_a_stored_explore_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_config(
        FileConfig(
            logfire_read_key="lf-read",
            logfire_write_key="lf-write",
            logfire_explore_url="https://logfire-us.pydantic.dev/other/project/explore",
        )
    )

    async def _derived(key: str | None = None) -> LogfireLinks:
        if key == "lf-read":
            return _DERIVED_SOURCE
        if key == "lf-write":
            return _DERIVED_VALCORE
        raise AssertionError(f"unexpected key {key!r}")

    monkeypatch.setattr("valcore.logfire_links.resolve_logfire_links", _derived)
    body = await _get_setup(create_app())
    assert body["logfire_explore_url"] == _DERIVED_SOURCE.explore_url
    assert body["logfire_traces_url"] == _DERIVED_SOURCE.traces_url
    assert body["logfire_datasets_url"] == _DERIVED_VALCORE.datasets_url


@pytest.mark.anyio
async def test_setup_does_not_take_datasets_url_from_the_read_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hosted datasets live on the valcore project; a read-only config must not link there."""
    save_config(FileConfig(logfire_read_key="lf-read"))

    async def _derived(key: str | None = None) -> LogfireLinks:
        assert key == "lf-read"
        return _DERIVED_SOURCE

    monkeypatch.setattr("valcore.logfire_links.resolve_logfire_links", _derived)
    body = await _get_setup(create_app())
    assert body["logfire_explore_url"] == _DERIVED_SOURCE.explore_url
    assert body["logfire_traces_url"] == _DERIVED_SOURCE.traces_url
    assert body["logfire_datasets_url"] is None


@pytest.mark.anyio
async def test_setup_falls_back_to_the_stored_explore_url_when_lookup_fails() -> None:
    save_config(
        FileConfig(
            logfire_read_key="lf-read",
            logfire_explore_url="https://logfire-us.pydantic.dev/duncan/agent-tracing/explore",
        )
    )
    body = await _get_setup(create_app())
    assert body["logfire_explore_url"] == (
        "https://logfire-us.pydantic.dev/duncan/agent-tracing/explore"
    )
    assert body["logfire_traces_url"] is None
    assert body["logfire_datasets_url"] is None


# -- No key value ever appears in the response ---------------------------------


@pytest.mark.anyio
async def test_no_secret_value_leaks_into_the_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression guard against a future field leaking a configured secret."""
    monkeypatch.setenv(GATEWAY_ENV, "sk-super-secret-gateway-value")
    save_config(
        FileConfig(
            logfire_token="lf-super-secret-token-value",
            logfire_read_key="lf-super-secret-read-value",
            logfire_write_key="lf-super-secret-write-value",
        )
    )
    async with _client(create_app()) as client:
        resp = await client.get("/api/setup")
    assert resp.status_code == 200, resp.text
    raw = resp.text
    for secret in (
        "sk-super-secret-gateway-value",
        "lf-super-secret-token-value",
        "lf-super-secret-read-value",
        "lf-super-secret-write-value",
    ):
        assert secret not in raw


# -- POST writes keys to the local config ---------------------------------------


async def _post_setup(app, payload: dict) -> httpx.Response:
    async with _client(app) as client:
        return await client.post("/api/setup", json=payload)


@pytest.mark.anyio
async def test_post_setup_persists_keys_and_returns_updated_presence() -> None:
    from valcore.config import load_config

    resp = await _post_setup(
        create_app(),
        {
            "gateway_api_key": "sk-posted-gateway",
            "logfire_token": "lf-posted-token",
            "logfire_read_key": "lf-posted-read",
            "logfire_write_key": "lf-posted-write",
        },
    )
    assert resp.status_code == 200, resp.text
    names = _by_name(resp.json())
    assert names["gateway_api_key"]["set"] is True
    assert names["logfire_token"]["set"] is True
    assert names["logfire_read_key"]["set"] is True
    assert names["logfire_write_key"]["set"] is True
    raw = resp.text
    for secret in (
        "sk-posted-gateway",
        "lf-posted-token",
        "lf-posted-read",
        "lf-posted-write",
    ):
        assert secret not in raw

    cfg = load_config()
    assert cfg.gateway_api_key == "sk-posted-gateway"
    assert cfg.logfire_token == "lf-posted-token"
    assert cfg.logfire_read_key == "lf-posted-read"
    assert cfg.logfire_write_key == "lf-posted-write"


@pytest.mark.anyio
async def test_post_setup_omitted_fields_leave_existing_values() -> None:
    from valcore.config import load_config

    save_config(
        FileConfig(
            gateway_api_key="sk-keep",
            logfire_read_key="lf-keep-read",
        )
    )
    resp = await _post_setup(create_app(), {"logfire_write_key": "lf-new-write"})
    assert resp.status_code == 200, resp.text
    cfg = load_config()
    assert cfg.gateway_api_key == "sk-keep"
    assert cfg.logfire_read_key == "lf-keep-read"
    assert cfg.logfire_write_key == "lf-new-write"


@pytest.mark.anyio
async def test_post_setup_clear_unsets_only_named_keys() -> None:
    from valcore.config import load_config

    save_config(
        FileConfig(
            gateway_api_key="sk-keep",
            logfire_read_key="lf-read",
            logfire_write_key="lf-write",
        )
    )
    resp = await _post_setup(create_app(), {"clear": ["logfire_read_key"]})
    assert resp.status_code == 200, resp.text
    names = _by_name(resp.json())
    assert names["logfire_read_key"]["set"] is False
    assert names["logfire_write_key"]["set"] is True
    assert names["gateway_api_key"]["set"] is True
    cfg = load_config()
    assert cfg.logfire_read_key is None
    assert cfg.logfire_write_key == "lf-write"
    assert cfg.gateway_api_key == "sk-keep"


@pytest.mark.anyio
async def test_post_setup_blank_value_is_rejected() -> None:
    resp = await _post_setup(create_app(), {"gateway_api_key": "   "})
    assert resp.status_code == 422, resp.text


@pytest.mark.anyio
async def test_post_setup_clearing_read_on_legacy_key_keeps_write() -> None:
    from valcore.config import load_config

    save_config(FileConfig(logfire_api_key="lf-legacy-combined"))
    resp = await _post_setup(create_app(), {"clear": ["logfire_read_key"]})
    assert resp.status_code == 200, resp.text
    names = _by_name(resp.json())
    assert names["logfire_read_key"]["set"] is False
    assert names["logfire_write_key"]["set"] is True
    cfg = load_config()
    assert cfg.logfire_read_key is None
    assert cfg.logfire_write_key == "lf-legacy-combined"
    assert cfg.logfire_api_key is None


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
