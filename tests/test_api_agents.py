"""Tests for the agent API: versioned definitions, trials, and saved response overlays.

The API must keep agent execution separate from evaluator runs: trials are ephemeral until a
caller explicitly saves their responses as a derivation joined to the source dataset.
"""

from collections.abc import Iterator

import httpx
import pytest
import yaml
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from sqlmodel import select

from valcore.api.deps import get_store
from valcore.api.main import create_app
from valcore.models import DerivationRole, DerivationState, DerivationStatus, RunKind, ScoreKind
from valcore.store import Store, create_engine, init_db, session_scope

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


@pytest.mark.anyio
async def test_version_without_input_binding_uses_optional_defaults(store: Store) -> None:
    async with _client(store) as client:
        agent = (await client.post("/api/agents", json={"name": "Helper"})).json()
        response = await client.post(
            f"/api/agents/{agent['id']}/versions",
            json={"version_name": "v1", "model": MODEL, "spec": SPEC},
        )
    assert response.status_code == 200, response.text
    assert response.json()["prompt_template"] == ""
    assert response.json()["required_columns"] == []


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


@pytest.mark.anyio
async def test_import_rejects_invalid_documents_and_ignores_non_mapping_binding(
    store: Store,
) -> None:
    """Imports expose parse failures and do not trust malformed transport metadata."""
    async with _client(store) as client:
        invalid = await client.post(
            "/api/agents/import", json={"content": "not: [valid", "format": "yaml"}
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["type"] == "ContractError"

        imported = await client.post(
            "/api/agents/import",
            json={
                "content": "instructions: Be helpful.\nmetadata:\n  valcore: not-a-mapping\n",
                "format": "yaml",
            },
        )
        assert imported.status_code == 200, imported.text
        assert imported.json() | {"spec": None} == {
            "spec": None,
            "model": None,
            "prompt_template": None,
            "required_columns": [],
            "deps_mapping": {},
        }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "binding",
    [
        {"model": 123},
        {"prompt_template": ["not", "text"]},
        {"required_columns": "not-a-list"},
        {"required_columns": ["valid", 123]},
        {"deps_mapping": "not-a-mapping"},
        {"deps_mapping": {"dep": 123}},
    ],
)
async def test_import_rejects_malformed_binding_fields(store: Store, binding: dict) -> None:
    """Malformed individual binding values are contract errors rather than server failures."""
    spec = AgentSpec(instructions="Be helpful.", metadata={"valcore": binding})
    content = yaml.safe_dump(
        spec.model_dump(mode="json", exclude_none=True, context={"use_short_form": True})
    )
    async with _client(store) as client:
        imported = await client.post(
            "/api/agents/import", json={"content": content, "format": "yaml"}
        )

    assert imported.status_code == 422, imported.text
    assert imported.json()["error"]["type"] == "ContractError"


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
async def test_local_trial_skips_gateway_key_guard(store: Store, monkeypatch) -> None:
    """A local CLI binding must remain runnable when no gateway credential is configured."""
    monkeypatch.delenv("PYDANTIC_AI_GATEWAY_API_KEY")
    monkeypatch.setattr(
        "valcore.api.routes.agents.config.require_gateway_key",
        lambda: pytest.fail("local CLI trials must not require a gateway key"),
    )
    monkeypatch.setattr(
        "valcore.api.routes.agents.build_agent_from_version",
        lambda version: PydanticAgent(TestModel(), output_type=str),
    )
    async with _client(store) as client:
        created = await client.post("/api/agents", json={"name": "Local agent"})
        version = await client.post(
            f"/api/agents/{created.json()['id']}/versions",
            json=_version_body(model="local/codex"),
        )
        trial = await client.post(
            f"/api/agents/versions/{version.json()['id']}/trial",
            json={"inputs": {"question": "Hi"}},
        )
        assert trial.status_code == 200, trial.text
        assert trial.json()["error"] is None


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


@pytest.mark.anyio
async def test_failed_derivation_save_does_not_append_adhoc_rows(store: Store) -> None:
    """All stored-row references are validated before any ad-hoc source row is committed."""
    dataset = store.create_dataset("questions", "", ["question"])
    other_dataset = store.create_dataset("other", "", ["question"])
    foreign_row = store.add_rows(other_dataset.id, [{"question": "Wrong dataset"}])[0]
    rows_before = store.list_rows(dataset.id)
    derivations_before = store.list_derivations(dataset_id=dataset.id)

    async with _client(store) as client:
        _, version = await _create_agent_and_version(client)
        failed = await client.post(
            f"/api/agents/versions/{version['id']}/derivations",
            json={
                "dataset_id": dataset.id,
                "entries": [
                    {
                        "inputs": {"question": "Must not be appended"},
                        "data": {"response": "Orphaned response"},
                    },
                    {
                        "row_id": foreign_row.id,
                        "data": {"response": "Invalid response"},
                    },
                ],
            },
        )

    assert failed.status_code == 422, failed.text
    assert store.list_rows(dataset.id) == rows_before
    assert store.list_derivations(dataset_id=dataset.id) == derivations_before


@pytest.mark.anyio
async def test_save_staged_derivation_allocates_ordinal_and_cannot_be_repeated(
    store: Store,
) -> None:
    """Accepting a staged pass makes it visible once with its allocated history position."""
    dataset = store.create_dataset("questions", "", ["question"])
    async with _client(store) as client:
        _, version = await _create_agent_and_version(client)
        staged = store.create_staged_derivation(
            dataset_id=dataset.id,
            agent_version_id=version["id"],
            response_columns=["response"],
        )

        saved = await client.post(f"/api/agents/derivations/{staged.id}/save")
        assert saved.status_code == 200, saved.text
        assert saved.json()["id"] == staged.id
        assert saved.json()["ordinal"] == 0
        assert saved.json()["state"] == "saved"
        assert store.derivation_state(staged.id) is DerivationState.SAVED

        repeated = await client.post(f"/api/agents/derivations/{staged.id}/save")

    assert repeated.status_code == 422, repeated.text
    assert repeated.json()["error"]["type"] == "ContractError"


@pytest.mark.anyio
async def test_delete_derivation_discards_staged_but_preserves_evaluator_input(
    store: Store,
) -> None:
    """Discard removes unaccepted work, while an evaluator's saved input remains immutable."""
    dataset = store.create_dataset("questions", "", ["question"])
    row = store.add_rows(dataset.id, [{"question": "What is valcore?"}])[0]
    async with _client(store) as client:
        _, agent_version = await _create_agent_and_version(client)
        staged = store.create_staged_derivation(
            dataset_id=dataset.id,
            agent_version_id=agent_version["id"],
            response_columns=["response"],
        )

        discarded = await client.delete(f"/api/agents/derivations/{staged.id}")
        assert discarded.status_code == 204, discarded.text
        assert store.list_derivations(dataset_id=dataset.id, include_staged=True) == []

        saved = store.save_derivation(
            dataset_id=dataset.id,
            agent_version_id=agent_version["id"],
            response_columns=["response"],
            responses=[{"row_id": row.id, "data": {"response": "An answer."}}],
        )
        evaluator = store.create_evaluator("Response judge")
        evaluator_version = store.create_version(
            evaluator.id,
            version_name="v1",
            model=MODEL,
            instructions="Judge the response.",
            prompt_template="{question} {response}",
            required_columns=["question", "response"],
            output_fields=[
                {
                    "name": "verdict",
                    "type": "enum",
                    "description": "A verdict.",
                    "enum_values": ["pass", "fail"],
                }
            ],
            score_field="verdict",
            score_kind=ScoreKind.CATEGORICAL,
            score_labels=["pass", "fail"],
        )
        run = store.create_run(RunKind.EVAL, evaluator_version.id, dataset.id, concurrency=1)
        store.link_run_derivation(run.id, saved.id, DerivationRole.READS)

        referenced = await client.delete(f"/api/agents/derivations/{saved.id}")

    assert referenced.status_code == 409, referenced.text
    assert referenced.json()["error"]["type"] == "ReferencedError"
    assert store.get_derivation(saved.id).id == saved.id


@pytest.mark.anyio
async def test_list_derivations_filters_staged_and_reports_legacy_saved_state(store: Store) -> None:
    """Listing hides staged overlays by default and treats pre-status rows as saved."""
    dataset = store.create_dataset("questions", "", ["question"])
    row = store.add_rows(dataset.id, [{"question": "What is valcore?"}])[0]
    async with _client(store) as client:
        _, version = await _create_agent_and_version(client)
        saved = store.save_derivation(
            dataset_id=dataset.id,
            agent_version_id=version["id"],
            response_columns=["response"],
            responses=[{"row_id": row.id, "data": {"response": "An answer."}}],
        )
        staged = store.create_staged_derivation(
            dataset_id=dataset.id,
            agent_version_id=version["id"],
            response_columns=["response"],
        )
        with session_scope(store.engine) as session:
            status = session.exec(
                select(DerivationStatus).where(DerivationStatus.derivation_id == saved.id)
            ).one()
            session.delete(status)

        ordinary = await client.get("/api/agents/derivations", params={"dataset_id": dataset.id})
        inclusive = await client.get(
            "/api/agents/derivations",
            params={"dataset_id": dataset.id, "include_staged": "true"},
        )

    assert ordinary.status_code == 200, ordinary.text
    assert len(ordinary.json()) == 1
    assert ordinary.json()[0]["id"] == saved.id
    assert ordinary.json()[0]["state"] == "saved"
    assert inclusive.status_code == 200, inclusive.text
    assert {item["id"] for item in inclusive.json()} == {saved.id, staged.id}
    assert {item["id"]: item["state"] for item in inclusive.json()} == {
        saved.id: "saved",
        staged.id: "staged",
    }


# -- Prompt sync ---------------------------------------------------------------------------------
#
# The routes are thin: they call an ``AgentPromptSync`` service supplied by the
# ``valcore.api.deps.get_prompt_sync`` dependency, run it in a worker thread, and serialize its
# ``SyncStatus``. These tests substitute a recording fake service; nothing contacts Logfire.

SYNC_KEY = "pylf_v1_secret_write_key_value"
SYNC_FINGERPRINT = "fp-3a9c7e51d0b24f68-secret"
SYNC_KEYS = ("instructions", "input_template")


def _template(key: str, agent_id: str, **overrides: object):
    """Build one template record in the shared ``inspect`` shape."""
    from valcore.agent_prompt_sync import TemplateStatus

    fields: dict[str, object] = {
        "variable_name": f"valcore_agent_{agent_id}_{key}",
        "state": "in_sync",
        "local_text": "text",
        "base_text": "text",
        "remote_text": "text",
        "remote_version": 3,
        "base_remote_version": 3,
    }
    fields.update(overrides)
    return TemplateStatus(**fields)  # type: ignore[arg-type]


def _status(agent_id: str, *, revision: str = "rev-1", **overrides: object):
    """Build a ``SyncStatus`` for both templates; ``overrides`` apply to the status itself."""
    from valcore.agent_prompt_sync import SyncStatus

    fields: dict[str, object] = {
        "linked": True,
        "local_version_id": "ver-1",
        "revision": revision,
        "error": None,
        "templates": {key: _template(key, agent_id) for key in SYNC_KEYS},
    }
    fields.update(overrides)
    return SyncStatus(**fields)  # type: ignore[arg-type]


class FakeSyncService:
    """Records calls and returns/raises whatever a test configures."""

    def __init__(self, store: Store, agent_id: str) -> None:
        self.store = store
        self.agent_id = agent_id
        self.calls: list[tuple] = []
        self.result = _status(agent_id)
        self.raises: Exception | None = None
        self.key_fingerprint = SYNC_FINGERPRINT  # must never reach a response
        self.api_key = SYNC_KEY  # must never reach a response
        self.on_call = None  # optional hook run inside the call, e.g. to create a version
        self.threads: list[int] = []

    def _run(self, name: str, *args: object):
        import threading

        self.threads.append(threading.get_ident())
        self.calls.append((name, *args))
        if self.on_call is not None:
            self.on_call()
        if self.raises is not None:
            raise self.raises
        return self.result

    def inspect(self, agent_id: str):
        return self._run("inspect", agent_id)

    def link(self, agent_id: str, initial: str, expected_revision: str):
        return self._run("link", agent_id, initial, expected_revision)

    def pull(self, agent_id: str, fields, expected_revision: str):
        return self._run("pull", agent_id, fields, expected_revision)

    def push(self, agent_id: str, fields, expected_revision: str):
        return self._run("push", agent_id, fields, expected_revision)

    def resolve(self, agent_id: str, fields, choice: str, expected_revision: str):
        return self._run("resolve", agent_id, fields, choice, expected_revision)

    def unlink(self, agent_id: str, expected_revision: str):
        return self._run("unlink", agent_id, expected_revision)


def _sync_client(store: Store, service: FakeSyncService) -> httpx.AsyncClient:
    """Like ``_client`` but with the prompt-sync service dependency replaced by a fake."""
    from valcore.api.deps import get_prompt_sync

    app = create_app()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_prompt_sync] = lambda: service
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def sync_setup(store: Store):
    """An agent with one version plus a fake service bound to it."""
    agent = store.create_agent("Sync bot", "")
    version = store.create_agent_version(
        agent.id,
        version_name="v1",
        notes="",
        model=MODEL,
        spec=SPEC,
        prompt_template="Answer: {question}",
        required_columns=["question"],
        deps_mapping={},
    )
    service = FakeSyncService(store, agent.id)
    service.result = _status(agent.id, local_version_id=version.id)
    return agent, version, service


@pytest.mark.anyio
async def test_get_prompt_sync_returns_inspect_shape(store: Store, sync_setup) -> None:
    agent, version, service = sync_setup
    service.result = _status(
        agent.id,
        local_version_id=version.id,
        templates={
            "instructions": _template(
                "instructions",
                agent.id,
                state="conflict",
                local_text="mine",
                base_text="base",
                remote_text="theirs",
                remote_version=4,
                base_remote_version=3,
            ),
            "input_template": _template("input_template", agent.id, state="in_sync"),
        },
    )
    async with _sync_client(store, service) as client:
        response = await client.get(f"/api/agents/{agent.id}/prompt-sync")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["linked"] is True
    assert body["local_version_id"] == version.id
    assert body["revision"] == "rev-1"
    assert body["error"] is None
    assert set(body["templates"]) == set(SYNC_KEYS)
    assert body["templates"]["instructions"] == {
        "variable_name": f"valcore_agent_{agent.id}_instructions",
        "state": "conflict",
        "local_text": "mine",
        "base_text": "base",
        "remote_text": "theirs",
        "remote_version": 4,
        "base_remote_version": 3,
        "error": None,
    }
    assert body["templates"]["input_template"]["state"] == "in_sync"
    assert service.calls == [("inspect", agent.id)]


@pytest.mark.anyio
async def test_get_prompt_sync_for_unknown_agent_is_404(store: Store, sync_setup) -> None:
    _agent, _version, service = sync_setup
    async with _sync_client(store, service) as client:
        response = await client.get("/api/agents/does-not-exist/prompt-sync")
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "NotFoundError"


@pytest.mark.anyio
async def test_get_prompt_sync_without_key_reports_configuration_error(
    store: Store, sync_setup
) -> None:
    """No key configured is a 200 status with an error and no remote state to show."""
    agent, version, service = sync_setup
    service.result = _status(
        agent.id,
        linked=False,
        local_version_id=version.id,
        error=(
            "Configure a Logfire API key with project:read_variables and "
            "project:write_variables scopes."
        ),
        templates={
            key: _template(
                key,
                agent.id,
                state="unsupported",
                remote_text=None,
                remote_version=None,
                base_text=None,
                base_remote_version=None,
            )
            for key in SYNC_KEYS
        },
    )
    async with _sync_client(store, service) as client:
        response = await client.get(f"/api/agents/{agent.id}/prompt-sync")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "Configure a Logfire API key" in body["error"]
    assert "project:read_variables" in body["error"]
    assert "project:write_variables" in body["error"]
    assert all(t["remote_text"] is None for t in body["templates"].values())


@pytest.mark.anyio
async def test_get_prompt_sync_after_key_rotation_shows_rebind_error(
    store: Store, sync_setup
) -> None:
    agent, version, service = sync_setup
    service.result = _status(
        agent.id,
        local_version_id=version.id,
        error="The configured Logfire key changed; unlink before linking the new project.",
        templates={
            key: _template(
                key, agent.id, state="unsupported", remote_text=None, remote_version=None
            )
            for key in SYNC_KEYS
        },
    )
    async with _sync_client(store, service) as client:
        response = await client.get(f"/api/agents/{agent.id}/prompt-sync")
    assert response.status_code == 200
    assert "unlink" in response.json()["error"]
    assert all(t["remote_text"] is None for t in response.json()["templates"].values())


@pytest.mark.anyio
async def test_link_posts_initial_and_revision(store: Store, sync_setup) -> None:
    agent, _version, service = sync_setup
    async with _sync_client(store, service) as client:
        response = await client.post(
            f"/api/agents/{agent.id}/prompt-sync/link",
            json={"initial": "remote", "expected_revision": "rev-1"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["linked"] is True
    assert service.calls == [("link", agent.id, "remote", "rev-1")]


@pytest.mark.anyio
async def test_link_rejects_invalid_initial_choice(store: Store, sync_setup) -> None:
    agent, _version, service = sync_setup
    async with _sync_client(store, service) as client:
        response = await client.post(
            f"/api/agents/{agent.id}/prompt-sync/link",
            json={"initial": "both", "expected_revision": "rev-1"},
        )
    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["link", "pull", "push", "resolve"])
async def test_mutations_require_expected_revision(store: Store, sync_setup, action: str) -> None:
    agent, _version, service = sync_setup
    body: dict[str, object] = {"initial": "local", "fields": ["instructions"], "choice": "local"}
    body = {k: v for k, v in body.items() if action != "link" or k == "initial"}
    async with _sync_client(store, service) as client:
        response = await client.post(f"/api/agents/{agent.id}/prompt-sync/{action}", json=body)
    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["pull", "push"])
async def test_pull_and_push_default_to_all_eligible_fields(
    store: Store, sync_setup, action: str
) -> None:
    agent, _version, service = sync_setup
    async with _sync_client(store, service) as client:
        response = await client.post(
            f"/api/agents/{agent.id}/prompt-sync/{action}", json={"expected_revision": "rev-1"}
        )
    assert response.status_code == 200, response.text
    assert service.calls == [(action, agent.id, None, "rev-1")]


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["pull", "push"])
async def test_pull_and_push_forward_a_field_subset(store: Store, sync_setup, action: str) -> None:
    agent, _version, service = sync_setup
    async with _sync_client(store, service) as client:
        response = await client.post(
            f"/api/agents/{agent.id}/prompt-sync/{action}",
            json={"fields": ["input_template"], "expected_revision": "rev-1"},
        )
    assert response.status_code == 200, response.text
    assert service.calls == [(action, agent.id, ["input_template"], "rev-1")]


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["pull", "push"])
async def test_pull_and_push_reject_unknown_field_names(
    store: Store, sync_setup, action: str
) -> None:
    agent, _version, service = sync_setup
    async with _sync_client(store, service) as client:
        response = await client.post(
            f"/api/agents/{agent.id}/prompt-sync/{action}",
            json={"fields": ["model"], "expected_revision": "rev-1"},
        )
    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.anyio
async def test_pull_response_includes_new_active_version_id(store: Store, sync_setup) -> None:
    agent, version, service = sync_setup
    created: list[str] = []

    def create_pulled_version() -> None:
        new = store.create_agent_version(
            agent.id,
            version_name="v2 (Logfire sync)",
            notes="",
            model=MODEL,
            spec={"instructions": "Pulled instructions."},
            prompt_template="Answer: {question}",
            required_columns=["question"],
            deps_mapping={},
        )
        created.append(new.id)
        service.result = _status(agent.id, local_version_id=new.id)

    service.on_call = create_pulled_version
    async with _sync_client(store, service) as client:
        response = await client.post(
            f"/api/agents/{agent.id}/prompt-sync/pull", json={"expected_revision": "rev-1"}
        )
    assert response.status_code == 200, response.text
    assert created and created[0] != version.id
    assert response.json()["active_version_id"] == created[0]


@pytest.mark.anyio
async def test_pull_response_uses_service_version_when_active_version_changes_after_return(
    store: Store, sync_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    from valcore.api.routes import agents as agents_routes

    agent, _version, service = sync_setup
    pulled = store.create_agent_version(
        agent.id,
        version_name="pulled",
        notes="",
        model=MODEL,
        spec=SPEC,
        prompt_template="Answer: {question}",
        required_columns=["question"],
        deps_mapping={},
    )
    service.result = _status(agent.id, local_version_id=pulled.id)
    original_run_in_threadpool = agents_routes.run_in_threadpool
    competing_version_ids: list[str] = []

    async def change_active_after_service_returns(func, *args):
        status = await original_run_in_threadpool(func, *args)
        competing = store.create_agent_version(
            agent.id,
            version_name="competing edit",
            notes="",
            model=MODEL,
            spec=SPEC,
            prompt_template="Answer: {question}",
            required_columns=["question"],
            deps_mapping={},
        )
        competing_version_ids.append(competing.id)
        return status

    monkeypatch.setattr(agents_routes, "run_in_threadpool", change_active_after_service_returns)
    async with _sync_client(store, service) as client:
        response = await client.post(
            f"/api/agents/{agent.id}/prompt-sync/pull", json={"expected_revision": "rev-1"}
        )
    assert response.status_code == 200, response.text
    assert (
        competing_version_ids
        and store.get_agent(agent.id).active_version_id == competing_version_ids[0]
    )
    assert response.json()["local_version_id"] == pulled.id
    assert response.json()["active_version_id"] == pulled.id


@pytest.mark.anyio
async def test_resolve_posts_fields_choice_and_revision(store: Store, sync_setup) -> None:
    agent, _version, service = sync_setup
    async with _sync_client(store, service) as client:
        response = await client.post(
            f"/api/agents/{agent.id}/prompt-sync/resolve",
            json={"fields": ["instructions"], "choice": "remote", "expected_revision": "rev-1"},
        )
    assert response.status_code == 200, response.text
    assert service.calls == [("resolve", agent.id, ["instructions"], "remote", "rev-1")]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        {"fields": ["instructions"], "choice": "merge", "expected_revision": "rev-1"},
        {"fields": [], "choice": "local", "expected_revision": "rev-1"},
        {"choice": "local", "expected_revision": "rev-1"},
        {"fields": ["spec"], "choice": "local", "expected_revision": "rev-1"},
        {"fields": ["instructions"], "expected_revision": "rev-1"},
    ],
)
async def test_resolve_validates_body(store: Store, sync_setup, body: dict) -> None:
    agent, _version, service = sync_setup
    async with _sync_client(store, service) as client:
        response = await client.post(f"/api/agents/{agent.id}/prompt-sync/resolve", json=body)
    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.anyio
async def test_delete_prompt_sync_takes_revision_in_json_body(store: Store, sync_setup) -> None:
    agent, _version, service = sync_setup
    service.result = _status(agent.id, linked=False)
    async with _sync_client(store, service) as client:
        response = await client.request(
            "DELETE", f"/api/agents/{agent.id}/prompt-sync", json={"expected_revision": "rev-1"}
        )
    assert response.status_code == 200, response.text
    assert response.json()["linked"] is False
    assert service.calls == [("unlink", agent.id, "rev-1")]


@pytest.mark.anyio
async def test_delete_prompt_sync_requires_expected_revision(store: Store, sync_setup) -> None:
    agent, _version, service = sync_setup
    async with _sync_client(store, service) as client:
        response = await client.request("DELETE", f"/api/agents/{agent.id}/prompt-sync", json={})
    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.anyio
async def test_unlink_after_key_rotation_returns_local_only_status(
    store: Store, sync_setup
) -> None:
    """The service's local-only Unlink result passes through with no remote text."""
    agent, version, service = sync_setup
    service.result = _status(
        agent.id,
        linked=False,
        local_version_id=version.id,
        templates={
            key: _template(
                key,
                agent.id,
                state="remote_missing",
                remote_text=None,
                remote_version=None,
                base_text=None,
                base_remote_version=None,
            )
            for key in SYNC_KEYS
        },
    )
    async with _sync_client(store, service) as client:
        response = await client.request(
            "DELETE", f"/api/agents/{agent.id}/prompt-sync", json={"expected_revision": "rev-1"}
        )
    assert response.status_code == 200, response.text
    assert response.json()["linked"] is False
    assert all(t["remote_text"] is None for t in response.json()["templates"].values())


# (method, path suffix, body) for every mutating endpoint
_SYNC_MUTATIONS = [
    ("POST", "/link", {"initial": "local", "expected_revision": "rev-1"}),
    ("POST", "/pull", {"expected_revision": "rev-1"}),
    ("POST", "/push", {"expected_revision": "rev-1"}),
    (
        "POST",
        "/resolve",
        {"fields": ["instructions"], "choice": "local", "expected_revision": "rev-1"},
    ),
    ("DELETE", "", {"expected_revision": "rev-1"}),
]


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "suffix", "body"), _SYNC_MUTATIONS)
async def test_stale_revision_maps_to_409_in_error_envelope(
    store: Store, sync_setup, method: str, suffix: str, body: dict
) -> None:
    from valcore.errors import SyncConflictError

    agent, _version, service = sync_setup
    service.raises = SyncConflictError("Prompt sync state changed; inspect sync again.")
    async with _sync_client(store, service) as client:
        response = await client.request(
            method, f"/api/agents/{agent.id}/prompt-sync{suffix}", json=body
        )
    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "type": "SyncConflictError",
            "message": "Prompt sync state changed; inspect sync again.",
        }
    }


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "suffix", "body"), _SYNC_MUTATIONS)
async def test_unsupported_template_maps_to_422(
    store: Store, sync_setup, method: str, suffix: str, body: dict
) -> None:
    from valcore.errors import ConfigError

    agent, _version, service = sync_setup
    message = "input_template cannot round-trip exactly through Logfire format."
    service.raises = ConfigError(message)
    async with _sync_client(store, service) as client:
        response = await client.request(
            method, f"/api/agents/{agent.id}/prompt-sync{suffix}", json=body
        )
    assert response.status_code == 422
    assert response.json()["error"] == {"type": "ConfigError", "message": message}


@pytest.mark.anyio
async def test_underscoped_or_missing_key_error_is_422_and_leaks_nothing(
    store: Store, sync_setup
) -> None:
    from valcore.errors import ConfigError

    agent, _version, service = sync_setup
    service.raises = ConfigError(
        "Logfire variable sync requires an API key with "
        "project:read_variables and project:write_variables."
    )
    async with _sync_client(store, service) as client:
        response = await client.get(f"/api/agents/{agent.id}/prompt-sync")
    assert response.status_code == 422
    assert "project:read_variables" in response.json()["error"]["message"]
    assert "project:write_variables" in response.json()["error"]["message"]
    assert SYNC_KEY not in response.text
    assert SYNC_FINGERPRINT not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "suffix", "body"), _SYNC_MUTATIONS[:-1])
async def test_missing_key_mutation_error_names_both_scopes(
    store: Store, sync_setup, method: str, suffix: str, body: dict
) -> None:
    from valcore.errors import ConfigError

    agent, _version, service = sync_setup
    service.raises = ConfigError(
        "Configure a Logfire API key with project:read_variables and "
        "project:write_variables scopes."
    )
    async with _sync_client(store, service) as client:
        response = await client.request(
            method, f"/api/agents/{agent.id}/prompt-sync{suffix}", json=body
        )
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "ConfigError"
    assert "project:read_variables" in response.json()["error"]["message"]
    assert "project:write_variables" in response.json()["error"]["message"]


@pytest.mark.anyio
async def test_responses_contain_no_credentials_or_fingerprint(store: Store, sync_setup) -> None:
    agent, _version, service = sync_setup
    async with _sync_client(store, service) as client:
        responses = [
            await client.get(f"/api/agents/{agent.id}/prompt-sync"),
            await client.post(
                f"/api/agents/{agent.id}/prompt-sync/link",
                json={"initial": "local", "expected_revision": "rev-1"},
            ),
            await client.post(
                f"/api/agents/{agent.id}/prompt-sync/push", json={"expected_revision": "rev-1"}
            ),
        ]
    for response in responses:
        assert response.status_code == 200, response.text
        assert SYNC_KEY not in response.text
        assert SYNC_FINGERPRINT not in response.text
        assert "fingerprint" not in response.text.lower()
        assert "authorization" not in response.text.lower()


@pytest.mark.anyio
async def test_slow_sync_service_runs_in_a_thread_without_blocking_the_event_loop(
    store: Store, sync_setup
) -> None:
    """A blocking adapter call must not stall other requests on the same event loop."""
    import asyncio
    import threading

    agent, _version, service = sync_setup
    release = threading.Event()
    outcome: dict[str, bool] = {}

    def block_until_released() -> None:
        # Times out (returns False) if the event loop is blocked and cannot serve /api/health.
        outcome["released"] = release.wait(timeout=5)

    service.on_call = block_until_released
    main_thread = threading.get_ident()
    async with _sync_client(store, service) as client:
        slow = asyncio.create_task(client.get(f"/api/agents/{agent.id}/prompt-sync"))
        await asyncio.sleep(0.2)
        health = await asyncio.wait_for(client.get("/api/health"), timeout=2)
        release.set()
        response = await slow
    assert health.status_code == 200
    assert response.status_code == 200
    assert outcome == {"released": True}
    assert service.threads and service.threads[0] != main_thread
