"""Specify the command-line workflow for versioned agents under test.

The agent CLI must run entirely against the local Store, so these tests replace
the live model builder with Pydantic AI's TestModel and exercise a real SQLite
database through Click's CliRunner.
"""

import json
from collections.abc import Iterator
from importlib import import_module
from pathlib import Path
from types import ModuleType

import pytest
from click.testing import CliRunner, Result
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.models.test import TestModel

from valcore.cli.main import cli
from valcore.models import Agent, AgentVersion
from valcore.store import Store, create_engine, init_db


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Return a fresh SQLite path for one CLI invocation set."""
    return tmp_path / "cli-agents.db"


@pytest.fixture
def store(db_path: Path) -> Iterator[Store]:
    """Yield a real Store whose tables match the CLI's local database."""
    engine = create_engine(db_path)
    init_db(engine)
    try:
        yield Store(engine)
    finally:
        engine.dispose()


@pytest.fixture
def runner() -> CliRunner:
    """Return Click's isolated CLI runner."""
    return CliRunner()


def _invoke(runner: CliRunner, db_path: Path, *args: str) -> Result:
    """Invoke the CLI against the test's explicit local database."""
    return runner.invoke(cli, ["--db", str(db_path), *args])


def _spec() -> dict:
    """Return the smallest serializable text-output AgentSpec fixture."""
    return AgentSpec(model="test", name="writer", instructions="Write a response.").model_dump(
        mode="json", context={"use_short_form": True}
    )


def _seed_agent(store: Store) -> tuple[Agent, AgentVersion]:
    """Create one agent and its active version with an input-column binding."""
    agent = store.create_agent("writer", "Writes a compact reply.")
    version = store.create_agent_version(
        agent.id,
        version_name="v1",
        model="local/codex",
        spec=_spec(),
        prompt_template="Reply to: {input}",
        required_columns=["input"],
        deps_mapping={},
    )
    return agent, version


def _test_agent_builder(version: AgentVersion) -> PydanticAgent:
    """Build a deterministic model so CLI trials never contact a provider."""
    return PydanticAgent(TestModel(custom_output_text="draft response"))


def _cli_main_module() -> ModuleType:
    """Return the CLI implementation module despite ``valcore.cli.main`` also being exported."""
    return import_module("valcore.cli.main")


def test_list_agents_empty_store_renders_agent_columns(runner: CliRunner, db_path: Path) -> None:
    """``list agents`` must be a useful empty local-store view."""
    result = _invoke(runner, db_path, "list", "agents")

    assert result.exit_code == 0
    assert "name" in result.output
    assert "version_count" in result.output
    assert "active_version" in result.output


def test_list_agents_includes_version_count_and_active_name(
    runner: CliRunner, store: Store, db_path: Path
) -> None:
    """Agent listings expose the current version without leaking implementation details."""
    agent, _ = _seed_agent(store)

    result = _invoke(runner, db_path, "list", "agents", "--json")

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == [
        {
            "id": agent.id,
            "name": "writer",
            "version_count": 1,
            "active_version": "v1",
        }
    ]


def test_agent_trial_with_ad_hoc_input_prints_response_and_writes_nothing(
    runner: CliRunner, store: Store, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unsaved ad-hoc trial is deliberately ephemeral."""
    _seed_agent(store)
    monkeypatch.setattr(_cli_main_module(), "build_agent_from_version", _test_agent_builder)

    result = _invoke(runner, db_path, "agent", "trial", "writer", "--input", "input=hello")

    assert result.exit_code == 0, result.output + result.stderr
    assert "Reply to: hello" in result.output
    assert "draft response" in result.output
    assert store.list_derivations() == []


def test_agent_trial_dataset_row_save_allocates_successive_ordinals(
    runner: CliRunner, store: Store, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saving repeated trials creates immutable overlays with save-time ordinals."""
    _, version = _seed_agent(store)
    dataset = store.create_dataset("cases", "", ["input"])
    source_row = store.add_rows(dataset.id, [{"input": "first"}])[0]
    monkeypatch.setattr(_cli_main_module(), "build_agent_from_version", _test_agent_builder)

    first = _invoke(
        runner, db_path, "agent", "trial", "writer", "--dataset", "cases", "--row", "0", "--save"
    )
    second = _invoke(
        runner, db_path, "agent", "trial", "writer", "--dataset", "cases", "--row", "0", "--save"
    )

    assert first.exit_code == 0, first.output + first.stderr
    assert second.exit_code == 0, second.output + second.stderr
    derivations = store.list_derivations(dataset_id=dataset.id, agent_version_id=version.id)
    assert [derivation.ordinal for derivation in derivations] == [0, 1]
    assert all(
        store.list_agent_responses(derivation.id)[0].dataset_row_id == source_row.id
        for derivation in derivations
    )


def test_agent_trial_save_requires_a_dataset(
    runner: CliRunner, store: Store, db_path: Path
) -> None:
    """A response cannot become a derivation without a source dataset row."""
    _seed_agent(store)

    result = _invoke(
        runner, db_path, "agent", "trial", "writer", "--input", "input=hello", "--save"
    )

    assert result.exit_code == 1
    assert "--save" in result.stderr
    assert "--dataset" in result.stderr


def test_agent_trial_row_requires_a_dataset(runner: CliRunner, store: Store, db_path: Path) -> None:
    """Row indices only make sense in the dataset whose rows they index."""
    _seed_agent(store)

    result = _invoke(runner, db_path, "agent", "trial", "writer", "--row", "0")

    assert result.exit_code == 1
    assert "--row" in result.stderr
    assert "--dataset" in result.stderr


def test_agent_trial_json_has_the_documented_result_contract(
    runner: CliRunner, store: Store, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Machine-readable trials always include output, diagnostics, and timing keys."""
    _seed_agent(store)
    monkeypatch.setattr(_cli_main_module(), "build_agent_from_version", _test_agent_builder)

    result = _invoke(
        runner, db_path, "agent", "trial", "writer", "--input", "input=hello", "--json"
    )

    assert result.exit_code == 0, result.output + result.stderr
    payload = json.loads(result.output)
    assert set(payload) == {"prompt", "deps", "output", "latency_ms", "usage", "error"}
    assert payload["prompt"] == "Reply to: hello"
    assert payload["output"] == {"response": "draft response"}
    assert payload["error"] is None


def test_agent_export_then_import_round_trips_spec_and_binding(
    runner: CliRunner,
    store: Store,
    db_path: Path,
    tmp_path: Path,
) -> None:
    """Agent artifacts preserve both opaque spec data and valcore's binding metadata."""
    _, version = _seed_agent(store)
    artifact = tmp_path / "writer-v1.yaml"

    exported = _invoke(runner, db_path, "agent", "export", "writer", "--out", str(artifact))

    assert exported.exit_code == 0, exported.output + exported.stderr
    exported_spec = AgentSpec.from_file(artifact)
    assert exported_spec.metadata is not None
    assert exported_spec.metadata["valcore"] == {
        "model": version.model,
        "prompt_template": version.prompt_template,
        "required_columns": version.required_columns,
        "deps_mapping": version.deps_mapping,
    }

    imported_db = tmp_path / "imported.db"
    imported = _invoke(
        runner, imported_db, "agent", "import", str(artifact), "--name", "writer copy"
    )

    assert imported.exit_code == 0, imported.output + imported.stderr
    engine = create_engine(imported_db)
    try:
        imported_store = Store(engine)
        imported_agent = imported_store.list_agents()[0]
        imported_version = imported_store.list_agent_versions(imported_agent.id)[0]
        assert imported_agent.name == "writer copy"
        assert imported_version.model == version.model
        assert imported_version.spec == version.spec
        assert imported_version.prompt_template == version.prompt_template
        assert imported_version.required_columns == version.required_columns
        assert imported_version.deps_mapping == version.deps_mapping
    finally:
        engine.dispose()


def test_agent_import_rejects_spec_without_valcore_binding(
    runner: CliRunner, db_path: Path, tmp_path: Path
) -> None:
    """Import must not invent dataset bindings absent from a portable AgentSpec."""
    artifact = tmp_path / "unbound.yaml"
    AgentSpec(model="test", name="unbound").to_file(artifact)

    result = _invoke(runner, db_path, "agent", "import", str(artifact))

    assert result.exit_code == 1
    assert "prompt_template" in result.stderr
    assert "required_columns" in result.stderr
