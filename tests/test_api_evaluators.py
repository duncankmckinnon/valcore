"""Tests for the evaluator API router: CRUD, versions, freeze, copy, generate, refine, export."""

import httpx
import pytest

from valcore import generator
from valcore.api.deps import get_store
from valcore.api.main import create_app
from valcore.generator import GeneratedConfig, RefinedConfig
from valcore.models import ScoreKind
from valcore.store import Store, create_engine, init_db


@pytest.fixture
def store(tmp_path) -> Store:
    """Return a Store backed by a throwaway SQLite database under tmp_path."""
    engine = create_engine(tmp_path / "test.db")
    init_db(engine)
    return Store(engine)


@pytest.fixture
def app(store: Store):
    """Return the FastAPI app with its store dependency pointed at the tmp_path DB."""
    application = create_app()
    application.dependency_overrides[get_store] = lambda: store
    return application


def _client(app) -> httpx.AsyncClient:
    """Return an ASGI-backed client bound to the given app."""
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _valid_version_body() -> dict:
    """Return a config body that passes both Pydantic parsing and store validation."""
    return {
        "version_name": "v1",
        "notes": "first",
        "model": "gateway/anthropic:claude-sonnet-5",
        "instructions": "Judge the answer.",
        "prompt_template": "Input: {input}\nOutput: {output}",
        "required_columns": ["input", "output"],
        "output_fields": [
            {"name": "reasoning", "type": "str", "description": "why"},
            {
                "name": "verdict",
                "type": "enum",
                "description": "the score",
                "enum_values": ["pass", "fail"],
            },
        ],
        "score_field": "verdict",
        "score_kind": "categorical",
        "score_labels": ["pass", "fail"],
        "capabilities": [{"name": "CodeMode", "config": {}}],
        "tools": [],
    }


def _canned_generated() -> GeneratedConfig:
    """Return a fully-populated GeneratedConfig used to stub the generator."""
    return GeneratedConfig(
        name="Answer quality",
        version_name="generated",
        instructions="Judge the answer.",
        prompt_template="Input: {input}\nOutput: {output}",
        required_columns=["input", "output"],
        output_fields=[
            {"name": "reasoning", "type": "str", "description": "why"},
            {
                "name": "verdict",
                "type": "enum",
                "description": "the score",
                "enum_values": ["pass", "fail"],
            },
        ],
        score_field="verdict",
        score_kind=ScoreKind.CATEGORICAL,
        score_labels=["pass", "fail"],
        capabilities=[{"name": "CodeMode", "config": {}}],
        tools=[],
        rationale="Categorical pass/fail is the natural schema.",
    )


# -- CRUD ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_full_crud_round_trip(app) -> None:
    async with _client(app) as client:
        # Empty to start.
        listed = await client.get("/api/evaluators")
        assert listed.status_code == 200
        assert listed.json() == []

        # Create.
        created = await client.post(
            "/api/evaluators", json={"name": "Quality", "description": "checks quality"}
        )
        assert created.status_code == 200
        evaluator = created.json()
        eval_id = evaluator["id"]
        assert evaluator["name"] == "Quality"
        assert evaluator["description"] == "checks quality"
        assert evaluator["active_version_id"] is None
        assert evaluator["active_version"] is None

        # List reflects it.
        listed = await client.get("/api/evaluators")
        assert [e["id"] for e in listed.json()] == [eval_id]

        # Get detail, no versions yet.
        detail = await client.get(f"/api/evaluators/{eval_id}")
        assert detail.status_code == 200
        assert detail.json()["versions"] == []

        # Add a version; it becomes the active version.
        made = await client.post(f"/api/evaluators/{eval_id}/versions", json=_valid_version_body())
        assert made.status_code == 200
        version = made.json()
        assert version["evaluator_id"] == eval_id
        assert version["frozen"] is False
        assert version["score_kind"] == "categorical"

        detail = await client.get(f"/api/evaluators/{eval_id}")
        body = detail.json()
        assert body["active_version_id"] == version["id"]
        assert [v["id"] for v in body["versions"]] == [version["id"]]

        # The list view now carries an active-version summary.
        listed = await client.get("/api/evaluators")
        summary = listed.json()[0]
        assert summary["active_version"]["id"] == version["id"]
        assert summary["active_version"]["frozen"] is False

        # Versions endpoint lists it.
        versions = await client.get(f"/api/evaluators/{eval_id}/versions")
        assert [v["id"] for v in versions.json()] == [version["id"]]

        # Delete.
        deleted = await client.delete(f"/api/evaluators/{eval_id}")
        assert deleted.status_code == 204

        gone = await client.get(f"/api/evaluators/{eval_id}")
        assert gone.status_code == 404
        assert gone.json()["error"]["type"] == "NotFoundError"


@pytest.mark.anyio
async def test_get_missing_evaluator_404(app) -> None:
    async with _client(app) as client:
        response = await client.get("/api/evaluators/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "NotFoundError"


# -- Version freeze / patch / copy --------------------------------------------


@pytest.mark.anyio
async def test_patch_unfrozen_then_frozen_returns_409(app, store: Store) -> None:
    async with _client(app) as client:
        evaluator = (await client.post("/api/evaluators", json={"name": "E"})).json()
        eval_id = evaluator["id"]
        version = (
            await client.post(f"/api/evaluators/{eval_id}/versions", json=_valid_version_body())
        ).json()
        vid = version["id"]

        # An unfrozen version can be patched.
        patched = await client.patch(f"/api/evaluators/versions/{vid}", json={"notes": "edited"})
        assert patched.status_code == 200
        assert patched.json()["notes"] == "edited"

        # Simulate a run freezing the version, then patching returns 409.
        store.freeze_version(vid)
        conflict = await client.patch(f"/api/evaluators/versions/{vid}", json={"notes": "again"})
        assert conflict.status_code == 409
        payload = conflict.json()
        assert payload["error"]["type"] == "FrozenVersionError"
        assert "new version" in payload["error"]["message"]


@pytest.mark.anyio
async def test_copy_produces_unfrozen_draft(app, store: Store) -> None:
    async with _client(app) as client:
        evaluator = (await client.post("/api/evaluators", json={"name": "E"})).json()
        eval_id = evaluator["id"]
        version = (
            await client.post(f"/api/evaluators/{eval_id}/versions", json=_valid_version_body())
        ).json()
        vid = version["id"]

        store.freeze_version(vid)

        copied = await client.post(f"/api/evaluators/versions/{vid}/copy")
        assert copied.status_code == 200
        draft = copied.json()
        assert draft["id"] != vid
        assert draft["frozen"] is False
        assert draft["evaluator_id"] == eval_id
        # The draft carries the same config as its source.
        assert draft["version_name"] == version["version_name"]
        assert draft["output_fields"] == version["output_fields"]
        assert draft["score_labels"] == version["score_labels"]

        # The copied draft can be edited (it is not frozen).
        patched = await client.patch(
            f"/api/evaluators/versions/{draft['id']}", json={"notes": "drafting"}
        )
        assert patched.status_code == 200


# -- Invalid config -----------------------------------------------------------


@pytest.mark.anyio
async def test_invalid_config_returns_422_uniform_body(app) -> None:
    async with _client(app) as client:
        evaluator = (await client.post("/api/evaluators", json={"name": "E"})).json()
        eval_id = evaluator["id"]

        body = _valid_version_body()
        body["output_fields"] = []  # passes Pydantic parsing but fails store validation

        response = await client.post(f"/api/evaluators/{eval_id}/versions", json=body)
    assert response.status_code == 422
    payload = response.json()
    assert set(payload) == {"error"}
    assert payload["error"]["type"] == "ConfigError"
    assert payload["error"]["message"]


# -- Generate / refine (monkeypatched, no network) ----------------------------


@pytest.mark.anyio
async def test_generate_returns_config_and_persists_nothing(app, store: Store, monkeypatch) -> None:
    canned = _canned_generated()

    async def fake_generate(criteria: str, *, columns=None) -> GeneratedConfig:
        assert criteria == "grade the answer"
        assert columns == ["input", "output"]
        return canned

    monkeypatch.setattr(generator, "generate_config", fake_generate)

    async with _client(app) as client:
        evaluator = (await client.post("/api/evaluators", json={"name": "E"})).json()
        eval_id = evaluator["id"]

        before = len(store.list_versions(eval_id))
        response = await client.post(
            f"/api/evaluators/{eval_id}/generate",
            json={"criteria": "grade the answer", "columns": ["input", "output"]},
        )
        after = len(store.list_versions(eval_id))

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Answer quality"
    assert payload["score_kind"] == "categorical"
    assert payload["rationale"]
    # Nothing was written: no version rows created, evaluator has no active version.
    assert before == 0
    assert after == 0
    assert store.get_evaluator(eval_id).active_version_id is None


@pytest.mark.anyio
async def test_generate_on_missing_evaluator_404(app, monkeypatch) -> None:
    async def fake_generate(criteria: str, *, columns=None) -> GeneratedConfig:
        return _canned_generated()

    monkeypatch.setattr(generator, "generate_config", fake_generate)

    async with _client(app) as client:
        response = await client.post("/api/evaluators/nope/generate", json={"criteria": "x"})
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "NotFoundError"


@pytest.mark.anyio
async def test_refine_returns_changed_fields(app, monkeypatch) -> None:
    refined = RefinedConfig(
        config=_canned_generated(),
        changed_fields=["instructions", "score_labels"],
        summary="Tightened the rubric.",
    )

    async def fake_refine(config: GeneratedConfig, instruction: str) -> RefinedConfig:
        assert instruction == "be stricter"
        assert config.name == "Answer quality"
        return refined

    monkeypatch.setattr(generator, "refine_config", fake_refine)

    async with _client(app) as client:
        response = await client.post(
            "/api/evaluators/refine",
            json={
                "config": _canned_generated().model_dump(mode="json"),
                "instruction": "be stricter",
            },
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["changed_fields"] == ["instructions", "score_labels"]
    assert payload["summary"] == "Tightened the rubric."
    assert payload["config"]["name"] == "Answer quality"


# -- Export -------------------------------------------------------------------


@pytest.mark.anyio
async def test_export_returns_compilable_source(app) -> None:
    async with _client(app) as client:
        evaluator = (await client.post("/api/evaluators", json={"name": "E"})).json()
        eval_id = evaluator["id"]
        version = (
            await client.post(f"/api/evaluators/{eval_id}/versions", json=_valid_version_body())
        ).json()

        response = await client.get(f"/api/evaluators/versions/{version['id']}/export")
    assert response.status_code == 200
    source = response.json()["source"]
    assert isinstance(source, str) and source.strip()
    # The exported script must be valid, compilable Python.
    compile(source, "<export>", "exec")
