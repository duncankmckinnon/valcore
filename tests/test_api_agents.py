"""Tests for the agent API: versioned definitions, trials, and saved response overlays.

The API must keep agent execution separate from evaluator runs: trials are ephemeral until a
caller explicitly saves their responses as a derivation joined to the source dataset.
"""

from collections.abc import Iterator

import httpx
import pytest
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from valcore.api.deps import get_store
from valcore.api.main import create_app
from valcore.store import Store, create_engine, init_db

MODEL = "gateway/anthropic:claude-sonnet-5"
SPEC = {"instructions": "Give a concise answer."}


# -- Fixtures & helpers -------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> Iterator[Store]:
    """Provide an isolated, file-backed store for each API test."""
    engine = create_engine(tmp_path / "agents.db")
    init_db(engine)
    try:
        yield Store(engine)
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _gateway_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make gateway-backed trial tests pass the route's credential guard."""
    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_API_KEY", "sk-test-gateway-key")


def _client(store: Store) -> httpx.AsyncClient:
    """Build an ASGI client whose store dependency targets the temporary database."""
    app = create_app()
    app.dependency_overrides[get_store] = lambda: store
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _version_body(**overrides: object) -> dict[str, object]:
    """Return the smallest valid agent-version request body, with optional overrides."""
    body: dict[str, object] = {
        "version_name": "first draft",
        "notes": "Initial definition.",
        "model": MODEL,
        "spec": SPEC,
        "prompt_template": "Answer: {question}",
        "required_columns": ["question"],
        "deps_mapping": {},
    }
    body.update(overrides)
    return body


async def _create_agent_and_version(client: httpx.AsyncClient) -> tuple[dict, dict]:
    """Create an agent and its initial version through the public API."""
    agent_response = await client.post(
        "/api/agents", json={"name": "Support bot", "description": "Answers questions."}
    )
    assert agent_response.status_code == 200, agent_response.text
    agent = agent_response.json()
    version_response = await client.post(
        f"/api/agents/{agent['id']}/versions", json=_version_body()
    )
    assert version_response.status_code == 200, version_response.text
    return agent, version_response.json()


# -- Agent and version lifecycle ---------------------------------------------


@pytest.mark.anyio
async def test_agent_crud_round_trip_and_missing_agent_is_404(store: Store) -> None:
    """Agents can be created, listed, read, patched, and deleted without versions."""
    async with _client(store) as client:
        created = await client.post(
            "/api/agents", json={"name": "Draft agent", "description": "Original."}
        )
        assert created.status_code == 200, created.text
        agent = created.json()
        assert agent["active_version_id"] is None
        assert agent["version_count"] == 0

        assert (await client.get("/api/agents")).json() == [agent]
        detail = await client.get(f"/api/agents/{agent['id']}")
        assert detail.status_code == 200
        assert detail.json()["agent"] == agent
        assert detail.json()["versions"] == []

        updated = await client.patch(f"/api/agents/{agent['id']}", json={"name": "Renamed"})
        assert updated.status_code == 200
        assert updated.json()["name"] == "Renamed"
        assert updated.json()["description"] == "Original."

        assert (await client.get("/api/agents/missing")).status_code == 404
        deleted = await client.delete(f"/api/agents/{agent['id']}")
        assert deleted.status_code == 204
        assert (await client.get(f"/api/agents/{agent['id']}")).status_code == 404


@pytest.mark.anyio
async def test_version_create_patch_freeze_copy_and_referenced_delete(store: Store) -> None:
    """Versions become active, preserve omitted patch fields, and honor lifecycle constraints."""
    dataset = store.create_dataset("questions", "", ["question"])
    row = store.add_rows(dataset.id, [{"question": "What is valcore?"}])[0]
    async with _client(store) as client:
        agent, version = await _create_agent_and_version(client)
        detail = (await client.get(f"/api/agents/{agent['id']}")).json()
        assert detail["agent"]["active_version_id"] == version["id"]
        assert detail["agent"]["version_count"] == 1
        assert version["response_columns"] == ["response"]

        patched = await client.patch(
            f"/api/agents/versions/{version['id']}", json={"notes": "Edited."}
        )
        assert patched.status_code == 200
        assert patched.json()["notes"] == "Edited."
        assert patched.json()["prompt_template"] == "Answer: {question}"
        assert patched.json()["required_columns"] == ["question"]

        frozen = await client.post(f"/api/agents/versions/{version['id']}/freeze")
        assert frozen.status_code == 200
        assert frozen.json()["frozen"] is True
        assert (
            await client.patch(f"/api/agents/versions/{version['id']}", json={"notes": "No"})
        ).status_code == 409

        copied = await client.post(
            f"/api/agents/versions/{version['id']}/copy", json={"version_name": "second draft"}
        )
        assert copied.status_code == 200
        assert copied.json()["id"] != version["id"]
        assert copied.json()["version_name"] == "second draft"
        assert copied.json()["frozen"] is False

        store.save_derivation(
            dataset_id=dataset.id,
            agent_version_id=version["id"],
            response_columns=["response"],
            responses=[{"row_id": row.id, "data": {"response": "An answer."}}],
        )
        blocked = await client.delete(f"/api/agents/versions/{version['id']}")
        assert blocked.status_code == 409
        assert blocked.json()["error"]["type"] == "ReferencedError"


# -- Portable definitions -----------------------------------------------------


@pytest.mark.anyio
async def test_export_and_import_round_trip_spec_and_binding(store: Store) -> None:
    """YAML export transports bindings in metadata without altering the stored spec blob."""
    async with _client(store) as client:
        _, version = await _create_agent_and_version(client)
        exported = await client.get(f"/api/agents/versions/{version['id']}/export")
        assert exported.status_code == 200, exported.text
        package = exported.json()
        assert package["filename"] == "support-bot-first-draft.yaml"

        parsed = AgentSpec.from_text(package["content"], "yaml")
        assert parsed.metadata["valcore"] == {
            "model": MODEL,
            "prompt_template": "Answer: {question}",
            "required_columns": ["question"],
            "deps_mapping": {},
        }

        imported = await client.post(
            "/api/agents/import", json={"content": package["content"], "format": "yaml"}
        )
        assert imported.status_code == 200, imported.text
        assert imported.json() == {
            "spec": parsed.model_dump(mode="json", context={"use_short_form": True}),
            "model": MODEL,
            "prompt_template": "Answer: {question}",
            "required_columns": ["question"],
            "deps_mapping": {},
        }


# -- Trials and derivations ---------------------------------------------------


@pytest.mark.anyio
async def test_trial_runs_stored_row_and_reports_model_failure(store: Store, monkeypatch) -> None:
    """Trials render stored rows, but execution failures remain inspectable 200 responses."""
    dataset = store.create_dataset("questions", "", ["question"])
    row = store.add_rows(dataset.id, [{"question": "What is valcore?"}])[0]
    monkeypatch.setattr(
        "valcore.api.routes.agents.build_agent_from_version",
        lambda version: PydanticAgent(TestModel(), output_type=str),
    )
    async with _client(store) as client:
        _, version = await _create_agent_and_version(client)
        trial = await client.post(
            f"/api/agents/versions/{version['id']}/trial",
            json={"dataset_id": dataset.id, "row_id": row.id},
        )
        assert trial.status_code == 200, trial.text
        body = trial.json()
        assert body["prompt"] == "Answer: What is valcore?"
        assert body["deps"] == {}
        assert body["response_columns"] == ["response"]
        assert body["output"]["response"]
        assert body["latency_ms"] >= 0
        assert body["error"] is None

        missing_input = await client.post(f"/api/agents/versions/{version['id']}/trial", json={})
        assert missing_input.status_code == 422

        def fail(_messages: list, _info: AgentInfo) -> str:
            raise RuntimeError("model unavailable")

        monkeypatch.setattr(
            "valcore.api.routes.agents.build_agent_from_version",
            lambda version: PydanticAgent(FunctionModel(fail), output_type=str),
        )
        failed = await client.post(
            f"/api/agents/versions/{version['id']}/trial", json={"inputs": {"question": "Hi"}}
        )
        assert failed.status_code == 200
        assert failed.json()["output"] == {}
        assert failed.json()["error"] == "model unavailable"


@pytest.mark.anyio
async def test_save_derivations_append_adhoc_rows_and_derived_rows_merge_columns(
    store: Store,
) -> None:
    """Saved responses overlay source rows, allocating sequential ordinals only when saved."""
    dataset = store.create_dataset("questions", "", ["question"])
    stored_row = store.add_rows(dataset.id, [{"question": "Stored question"}])[0]
    async with _client(store) as client:
        _, version = await _create_agent_and_version(client)
        first = await client.post(
            f"/api/agents/versions/{version['id']}/derivations",
            json={
                "dataset_id": dataset.id,
                "entries": [
                    {
                        "inputs": {"question": "Ad hoc question"},
                        "data": {"response": "Ad hoc answer"},
                        "latency_ms": 3,
                    }
                ],
            },
        )
        assert first.status_code == 200, first.text
        assert first.json()["ordinal"] == 0
        assert first.json()["response_columns"] == ["response"]

        second = await client.post(
            f"/api/agents/versions/{version['id']}/derivations",
            json={
                "dataset_id": dataset.id,
                "entries": [
                    {"row_id": stored_row.id, "data": {"response": "Stored answer"}},
                ],
            },
        )
        assert second.status_code == 200, second.text
        assert second.json()["ordinal"] == 1

        listed = await client.get("/api/agents/derivations", params={"dataset_id": dataset.id})
        assert [item["id"] for item in listed.json()] == [first.json()["id"], second.json()["id"]]

        rows = await client.get(f"/api/agents/derivations/{first.json()['id']}/rows")
        assert rows.status_code == 200, rows.text
        page = rows.json()
        assert page["columns"] == ["question", "response"]
        assert page["rows"] == [
            {
                "row_id": page["rows"][0]["row_id"],
                "idx": 1,
                "data": {"question": "Ad hoc question", "response": "Ad hoc answer"},
                "latency_ms": 3,
                "error": None,
            }
        ]
