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
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from valcore.cli.main import cli
from valcore.cli.resolve import resolve_agent, resolve_agent_version
from valcore.errors import ContractError, NotFoundError
from valcore.models import Agent, AgentVersion, DerivationRole, DerivationState, RunKind
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
            "id": agent.id[:8],
            "name": "writer",
            "version_count": 1,
            "active_version": "v1",
        }
    ]


def test_resolve_agent_accepts_a_unique_id_prefix(store: Store) -> None:
    """Agent references accept the same concise ID form as other CLI resources."""
    agent, _ = _seed_agent(store)

    assert resolve_agent(store, agent.id[:8]).id == agent.id


def test_resolve_agent_rejects_a_too_short_id_prefix(store: Store) -> None:
    """Short non-name references must not accidentally select an agent."""
    agent, _ = _seed_agent(store)

    with pytest.raises(NotFoundError, match="at least 4"):
        resolve_agent(store, agent.id[:3])


def test_resolve_agent_ambiguous_prefix_lists_candidates(store: Store) -> None:
    """Ambiguous agent prefixes identify every candidate instead of choosing one."""
    alpha = store.create_agent("alpha")
    beta = store.create_agent("beta")
    shared = "abcd1234"
    with store.engine.connect() as connection:
        from sqlalchemy import text

        connection.execute(
            text("UPDATE agent SET id = :new WHERE id = :old"),
            {"new": shared + "0" * 23 + "a", "old": alpha.id},
        )
        connection.execute(
            text("UPDATE agent SET id = :new WHERE id = :old"),
            {"new": shared + "0" * 23 + "b", "old": beta.id},
        )
        connection.commit()

    with pytest.raises(ContractError) as exc_info:
        resolve_agent(store, shared)

    assert "alpha" in str(exc_info.value)
    assert "beta" in str(exc_info.value)


def test_resolve_agent_missing_ref_names_it(store: Store) -> None:
    """Missing-agent errors preserve the requested reference for diagnosis."""
    with pytest.raises(NotFoundError, match="missing-agent"):
        resolve_agent(store, "missing-agent")


def test_resolve_agent_version_accepts_exact_name_and_id_prefix(store: Store) -> None:
    """Explicit agent versions resolve by exact version name or unique ID prefix."""
    agent, version = _seed_agent(store)

    assert resolve_agent_version(store, agent, "v1").id == version.id
    assert resolve_agent_version(store, agent, version.id[:8]).id == version.id


def test_resolve_agent_version_without_active_version_errors(store: Store) -> None:
    """Default version resolution clearly reports agents that have no active version."""
    agent = store.create_agent("empty")

    with pytest.raises(NotFoundError, match="Agent 'empty' has no active version"):
        resolve_agent_version(store, agent, None)


def test_run_agent_with_ad_hoc_input_prints_response_and_writes_nothing(
    runner: CliRunner, store: Store, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unsaved ad-hoc trial is deliberately ephemeral."""
    _seed_agent(store)
    monkeypatch.setattr(_cli_main_module(), "build_agent_from_version", _test_agent_builder)

    result = _invoke(runner, db_path, "run", "agent", "writer", "--input", "input=hello")

    assert result.exit_code == 0, result.output + result.stderr
    assert "Reply to: hello" in result.output
    assert "draft response" in result.output
    assert store.list_derivations() == []


def test_run_agent_dataset_row_save_allocates_successive_ordinals(
    runner: CliRunner, store: Store, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saving repeated trials creates immutable overlays with save-time ordinals."""
    _, version = _seed_agent(store)
    dataset = store.create_dataset("cases", "", ["input"])
    source_row = store.add_rows(dataset.id, [{"input": "first"}])[0]
    monkeypatch.setattr(_cli_main_module(), "build_agent_from_version", _test_agent_builder)

    first = _invoke(
        runner, db_path, "run", "agent", "writer", "--dataset", "cases", "--row", "0", "--save"
    )
    second = _invoke(
        runner, db_path, "run", "agent", "writer", "--dataset", "cases", "--row", "0", "--save"
    )

    assert first.exit_code == 0, first.output + first.stderr
    assert second.exit_code == 0, second.output + second.stderr
    derivations = store.list_derivations(dataset_id=dataset.id, agent_version_id=version.id)
    assert [derivation.ordinal for derivation in derivations] == [0, 1]
    assert all(
        store.list_agent_responses(derivation.id)[0].dataset_row_id == source_row.id
        for derivation in derivations
    )
    assert store.list_runs() == []


def test_run_agent_ad_hoc_input_with_dataset_save_appends_the_source_row(
    runner: CliRunner, store: Store, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saving an ad-hoc input attaches its response to the newly persisted dataset row."""
    _, version = _seed_agent(store)
    dataset = store.create_dataset("cases", "", ["input"])
    monkeypatch.setattr(_cli_main_module(), "build_agent_from_version", _test_agent_builder)

    result = _invoke(
        runner,
        db_path,
        "run",
        "agent",
        "writer",
        "--dataset",
        "cases",
        "--input",
        "input=ad hoc",
        "--save",
    )

    assert result.exit_code == 0, result.output + result.stderr
    rows = store.list_rows(dataset.id)
    assert [row.data for row in rows] == [{"input": "ad hoc"}]
    derivation = store.list_derivations(dataset_id=dataset.id, agent_version_id=version.id)[0]
    assert store.list_agent_responses(derivation.id)[0].dataset_row_id == rows[0].id
    assert store.list_runs() == []


def test_run_agent_input_save_requires_a_dataset(
    runner: CliRunner, store: Store, db_path: Path
) -> None:
    """A response cannot become a derivation without a source dataset row."""
    _seed_agent(store)

    result = _invoke(runner, db_path, "run", "agent", "writer", "--input", "input=hello", "--save")

    assert result.exit_code == 1
    assert "--save" in result.stderr
    assert "--dataset" in result.stderr


def test_run_agent_row_requires_a_dataset(runner: CliRunner, store: Store, db_path: Path) -> None:
    """Row indices only make sense in the dataset whose rows they index."""
    _seed_agent(store)

    result = _invoke(runner, db_path, "run", "agent", "writer", "--row", "0")

    assert result.exit_code == 1
    assert "--row" in result.stderr
    assert "--dataset" in result.stderr


def test_run_agent_reports_model_failures_as_domain_errors(
    runner: CliRunner, store: Store, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider exceptions must use the CLI's normal error channel and exit status."""
    _seed_agent(store)

    def failing_builder(version: AgentVersion) -> PydanticAgent:
        """Raise the provider failure that a real agent build/run may surface."""
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(_cli_main_module(), "build_agent_from_version", failing_builder)

    result = _invoke(runner, db_path, "run", "agent", "writer", "--input", "input=hello")

    assert result.exit_code == 1
    assert "error: model unavailable" in result.stderr


def test_run_agent_input_json_has_the_documented_result_contract(
    runner: CliRunner, store: Store, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Machine-readable trials always include output, diagnostics, and timing keys."""
    _seed_agent(store)
    monkeypatch.setattr(_cli_main_module(), "build_agent_from_version", _test_agent_builder)

    result = _invoke(runner, db_path, "run", "agent", "writer", "--input", "input=hello", "--json")

    assert result.exit_code == 0, result.output + result.stderr
    payload = json.loads(result.output)
    assert set(payload) == {"prompt", "deps", "output", "latency_ms", "usage", "error"}
    assert payload["prompt"] == "Reply to: hello"
    assert payload["output"] == {"response": "draft response"}
    assert payload["error"] is None


def test_run_agent_dataset_creates_a_staged_derive_run_and_prints_its_ref(
    runner: CliRunner, store: Store, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dataset pass is the sole agent mode that creates a first-class derive run."""
    _seed_agent(store)
    dataset = store.create_dataset("cases", "", ["input"])
    store.add_rows(dataset.id, [{"input": "first"}, {"input": "second"}])
    monkeypatch.setattr("valcore.runner.build_agent_from_version", _test_agent_builder)

    result = _invoke(runner, db_path, "run", "agent", "writer", "--dataset", "cases")

    assert result.exit_code == 0, result.output + result.stderr
    runs = store.list_runs()
    assert len(runs) == 1
    assert runs[0].kind is RunKind.DERIVE
    derivations = store.list_derivations(dataset_id=dataset.id, include_staged=True)
    assert len(derivations) == 1
    assert store.get_run_derivation(runs[0].id).role is DerivationRole.FILLS
    assert store.derivation_state(derivations[0].id) is DerivationState.STAGED
    assert derivations[0].id[:8] in result.output

    accepted = _invoke(runner, db_path, "agent", "derivation", "save", derivations[0].id[:8])

    assert accepted.exit_code == 0, accepted.output + accepted.stderr
    assert store.derivation_state(derivations[0].id) is DerivationState.SAVED


def test_run_agent_dataset_save_accepts_the_completed_derivation(
    runner: CliRunner, store: Store, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--save`` accepts the whole completed overlay rather than allocating another one."""
    _, version = _seed_agent(store)
    dataset = store.create_dataset("cases", "", ["input"])
    store.add_rows(dataset.id, [{"input": "first"}])
    monkeypatch.setattr("valcore.runner.build_agent_from_version", _test_agent_builder)

    result = _invoke(
        runner,
        db_path,
        "run",
        "agent",
        "writer",
        "--dataset",
        "cases",
        "--save",
        "--concurrency",
        "3",
    )

    assert result.exit_code == 0, result.output + result.stderr
    derivation = store.list_derivations(dataset_id=dataset.id, agent_version_id=version.id)[0]
    assert store.derivation_state(derivation.id) is DerivationState.SAVED
    assert derivation.ordinal == 0
    assert store.list_runs()[0].concurrency == 3


def test_run_agent_dataset_json_is_one_document_with_derivation_metadata(
    runner: CliRunner, store: Store, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Machine-readable dataset passes include the overlay without preceding prose."""
    _seed_agent(store)
    store.create_dataset("cases", "", ["input"])
    monkeypatch.setattr("valcore.runner.build_agent_from_version", _test_agent_builder)

    result = _invoke(runner, db_path, "run", "agent", "writer", "--dataset", "cases", "--json")

    assert result.exit_code == 0, result.output + result.stderr
    payload = json.loads(result.stdout)
    assert payload["kind"] == "derive"
    assert payload["derivation"]["state"] == "staged"
    assert payload["derivation"]["ref"] == payload["derivation"]["id"][:8]


def test_run_agent_prompt_bypasses_the_version_prompt_template(
    runner: CliRunner, store: Store, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Liveness prompts reach the configured agent literally, with no dataset interpolation."""
    _seed_agent(store)
    prompts: list[str] = []

    def capture(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        """Record the final user message and return a text response."""
        prompts.append(str(messages[-1].parts[0].content))
        return ModelResponse(parts=[TextPart(content="alive")])

    monkeypatch.setattr(
        _cli_main_module(),
        "build_agent_from_version",
        lambda version: PydanticAgent(FunctionModel(capture)),
    )

    result = _invoke(runner, db_path, "run", "agent", "writer", "--prompt", "raw probe")

    assert result.exit_code == 0, result.output + result.stderr
    assert prompts == ["raw probe"]
    assert store.list_runs() == []


def test_run_agent_prompt_does_not_map_row_dependencies(
    runner: CliRunner, store: Store, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Literal prompt mode runs without row data even when the stored binding maps dependencies."""
    agent, _ = _seed_agent(store)
    spec = _spec()
    spec["deps_schema"] = {
        "type": "object",
        "properties": {"context": {"type": "string"}},
    }
    store.create_agent_version(
        agent.id,
        version_name="with-deps",
        model="local/codex",
        spec=spec,
        prompt_template="Reply to: {input}",
        required_columns=["input"],
        deps_mapping={"context": "input"},
    )
    prompts: list[str] = []

    def capture(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        """Capture the literal prompt without consulting dependencies."""
        prompts.append(str(messages[-1].parts[0].content))
        return ModelResponse(parts=[TextPart(content="alive")])

    monkeypatch.setattr(
        _cli_main_module(),
        "build_agent_from_version",
        lambda version: PydanticAgent(FunctionModel(capture)),
    )

    result = _invoke(
        runner,
        db_path,
        "run",
        "agent",
        "writer",
        "--version",
        "with-deps",
        "-p",
        "raw probe",
    )

    assert result.exit_code == 0, result.output + result.stderr
    assert prompts == ["raw probe"]


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("--row", "0"), "--dataset"),
        (("--prompt", "probe", "--dataset", "cases"), "--prompt"),
        (("--prompt", "probe", "--input", "input=value"), "--prompt"),
        (("--prompt", "probe", "--save"), "--save"),
    ],
)
def test_run_agent_rejects_incompatible_mode_options(
    runner: CliRunner, store: Store, db_path: Path, args: tuple[str, ...], expected: str
) -> None:
    """Each mutually exclusive agent mode fails before it can invoke a model."""
    _seed_agent(store)

    result = _invoke(runner, db_path, "run", "agent", "writer", *args)

    assert result.exit_code == 1
    assert expected in result.stderr


def test_run_agent_rejects_row_and_ad_hoc_inputs_together(
    runner: CliRunner, store: Store, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row trial and an ad-hoc-input trial are distinct command modes."""
    _seed_agent(store)
    dataset = store.create_dataset("cases", "", ["input"])
    store.add_rows(dataset.id, [{"input": "stored"}])
    monkeypatch.setattr(_cli_main_module(), "build_agent_from_version", _test_agent_builder)

    result = _invoke(
        runner,
        db_path,
        "run",
        "agent",
        "writer",
        "--dataset",
        "cases",
        "--row",
        "0",
        "--input",
        "input=ignored",
    )

    assert result.exit_code == 1
    assert "--row" in result.stderr
    assert "--input" in result.stderr


def test_agent_derivation_commands_list_mark_save_and_discard_staged_entries(
    runner: CliRunner, store: Store, db_path: Path
) -> None:
    """Staged overlays stay manageable after a dataset pass finishes."""
    _, version = _seed_agent(store)
    dataset = store.create_dataset("cases", "", ["input"])
    first = store.create_staged_derivation(
        dataset_id=dataset.id, agent_version_id=version.id, response_columns=["response"]
    )
    second = store.create_staged_derivation(
        dataset_id=dataset.id, agent_version_id=version.id, response_columns=["response"]
    )

    listed = _invoke(runner, db_path, "agent", "derivation", "list", "--dataset", "cases")
    saved = _invoke(runner, db_path, "agent", "derivation", "save", first.id[:8])
    discarded = _invoke(runner, db_path, "agent", "derivation", "discard", second.id[:8])

    assert listed.exit_code == 0, listed.output + listed.stderr
    assert "staged" in listed.output.lower()
    assert saved.exit_code == 0, saved.output + saved.stderr
    assert store.derivation_state(first.id) is DerivationState.SAVED
    assert discarded.exit_code == 0, discarded.output + discarded.stderr
    assert [d.id for d in store.list_derivations(include_staged=True)] == [first.id]


def test_agent_derivation_discard_rejects_a_saved_derivation(
    runner: CliRunner, store: Store, db_path: Path
) -> None:
    """Discard is limited to staged passes and cannot delete an accepted overlay."""
    _, version = _seed_agent(store)
    dataset = store.create_dataset("cases", "", ["input"])
    row = store.add_rows(dataset.id, [{"input": "stored"}])[0]
    saved = store.save_derivation(
        dataset_id=dataset.id,
        agent_version_id=version.id,
        response_columns=["response"],
        responses=[{"row_id": row.id, "data": {"response": "answer"}}],
    )

    result = _invoke(runner, db_path, "agent", "derivation", "discard", saved.id[:8])

    assert result.exit_code == 1
    assert store.list_derivations()[0].id == saved.id


def test_agent_trial_is_removed(runner: CliRunner, store: Store, db_path: Path) -> None:
    """The former single-row command must not survive as a compatibility alias."""
    _seed_agent(store)

    result = _invoke(runner, db_path, "agent", "trial", "writer", "--input", "input=hello")

    assert result.exit_code != 0


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


def test_agent_import_invalid_binding_writes_no_orphaned_agent(
    runner: CliRunner, store: Store, db_path: Path, tmp_path: Path
) -> None:
    """The complete imported version is validated before its parent agent is persisted."""
    artifact = tmp_path / "invalid-binding.yaml"
    AgentSpec(
        model="test",
        name="invalid",
        metadata={
            "valcore": {
                "model": None,
                "prompt_template": "Reply to: {input}",
                "required_columns": ["input"],
                "deps_mapping": {},
            }
        },
    ).to_file(artifact)

    result = _invoke(runner, db_path, "agent", "import", str(artifact))

    assert result.exit_code == 1
    assert "Invalid valcore binding" in result.stderr
    assert store.list_agents() == []


def test_agent_import_malformed_binding_type_writes_no_orphaned_agent(
    runner: CliRunner, store: Store, db_path: Path, tmp_path: Path
) -> None:
    """Non-JSON binding containers fail before the parent agent is committed."""
    artifact = tmp_path / "set-binding.yaml"
    artifact.write_text(
        """\
model: test
name: invalid
metadata:
  valcore:
    model: local/codex
    prompt_template: 'Reply to: {input}'
    required_columns: !!set
      input:
    deps_mapping: {}
"""
    )

    result = _invoke(runner, db_path, "agent", "import", str(artifact))

    assert result.exit_code == 1
    assert "Invalid valcore binding" in result.stderr
    assert "required_columns must be a list of strings" in result.stderr
    assert store.list_agents() == []
