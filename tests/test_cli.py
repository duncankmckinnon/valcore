"""Tests for the ``valcore`` CLI.

No network and no real home directory: ``VALCORE_HOME`` is pointed at ``tmp_path``
by the autouse fixture in ``conftest.py``, the store is a fresh ``tmp_path`` SQLite
DB, and agent behavior for ``run`` is driven by a ``FunctionModel`` injected via a
monkeypatch of ``valcore.runner.build_agent``; ``experiment`` tests use ``TestModel``
against ``valcore.experiment.build_agent`` instead, per the test plan. ``logfire push``
is exercised by monkeypatching
``valcore.logfire_io.push_dataset`` with an async stub, following the module-qualified
call convention the task interfaces describe (``config.require_gateway_key()``,
``experiment.execute_experiment(...)``, ``logfire_io.push_dataset(...)``,
``tracing.configure_tracing(...)``) -- patching the source module's attribute works
regardless of how ``cli.main`` imports the module, as long as it calls through a
module reference rather than a name bound at import time.
"""

import json
from importlib.metadata import version as package_version

import pytest
from click.testing import CliRunner
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from valcore.cli.main import cli
from valcore.cli.resolve import resolve_dataset, resolve_evaluator, resolve_version
from valcore.config import load_config
from valcore.config_io import EvalPackage
from valcore.errors import ContractError, NotFoundError
from valcore.export import render_script
from valcore.factory import build_output_model
from valcore.models import LabelSource, OutputField, ScoreKind, parse_output_fields
from valcore.store import Store, create_engine, init_db

CATEGORICAL_SCHEMA = {"kind": "categorical", "labels": ["pass", "fail"]}

VERSION_FIELDS = {
    "version_name": "v1",
    "model": "gateway/anthropic:claude-sonnet-5",
    "instructions": "Judge the row.",
    "prompt_template": "Input: {input} Output: {output}",
    "required_columns": ["input", "output"],
    "output_fields": [
        {
            "name": "verdict",
            "type": "enum",
            "description": "pass or fail",
            "enum_values": ["pass", "fail"],
        }
    ],
    "score_field": "verdict",
    "score_kind": ScoreKind.CATEGORICAL,
    "score_labels": ["pass", "fail"],
}


@pytest.fixture
def db_path(tmp_path):
    """Path to a fresh SQLite database file under tmp_path."""
    return tmp_path / "cli.db"


@pytest.fixture
def store(db_path) -> Store:
    """A real Store backed by the fixture db path, with an evaluator and dataset seeded."""
    engine = create_engine(db_path)
    init_db(engine)
    store = Store(engine)
    _seed(store, labels=["pass", "fail", "pass", "fail"])
    return store


def _seed(store: Store, labels: list[str | None]) -> None:
    """Seed one evaluator (active version) and one dataset with the given row labels."""
    evaluator = store.create_evaluator("judge", description="a judge")
    store.create_version(evaluator.id, **VERSION_FIELDS)
    dataset = store.create_dataset("cases", "", ["input", "output"], CATEGORICAL_SCHEMA)
    rows = store.add_rows(
        dataset.id, [{"input": f"in{i}", "output": f"out{i}"} for i in range(len(labels))]
    )
    for row, label in zip(rows, labels, strict=True):
        if label is not None:
            store.set_label(row.id, {"value": label}, LabelSource.MANUAL)


def _constant_agent_builder(verdict: str = "pass"):
    """Return a ``build_agent`` replacement that always emits ``verdict``."""

    def build(version) -> Agent:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            name = info.output_tools[0].name
            return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"verdict": verdict})])

        return Agent(FunctionModel(respond), output_type=build_output_model(version))

    return build


def _constant_test_model_agent_builder(verdict: str = "pass"):
    """Return a ``build_agent`` replacement using ``TestModel`` that always emits ``verdict``.

    Used for the ``experiment`` tests per the test plan's ``TestModel`` requirement;
    ``TestModel(custom_output_args=...)`` pins the structured output without a network call.
    """

    def build(version) -> Agent:
        return Agent(
            TestModel(custom_output_args={"verdict": verdict}),
            output_type=build_output_model(version),
        )

    return build


def _invoke(runner: CliRunner, db_path, *args: str, **kwargs):
    """Invoke the CLI with ``--db`` bound to the test database."""
    return runner.invoke(cli, ["--db", str(db_path), *args], **kwargs)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _gateway_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Present a gateway key by default.

    ``run`` and ``experiment`` now guard on ``config.require_gateway_key()`` before doing any
    work. Without this, every pre-existing happy-path test below would fail on the guard before
    its injected agent ever ran -- mirroring the identical fixture in ``test_api_runs.py`` for the
    same guard on the API surface. Tests that target the guard itself clear the key explicitly.
    """
    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_API_KEY", "sk-test-gateway-key")


# -- version ------------------------------------------------------------------


def test_version(runner, db_path):
    result = _invoke(runner, db_path, "version")
    assert result.exit_code == 0
    assert result.output.strip() == package_version("valcore")


# -- tracing --------------------------------------------------------------------


def test_cli_group_configures_tracing_once_per_invocation(runner, db_path, monkeypatch):
    """The ``cli`` group callback must call ``configure_tracing(load_config())`` exactly once.

    Patches the whole function (not just its internals) so the module's own idempotency
    flag is irrelevant here -- this only checks that the CLI actually calls it, once, on
    every invocation, regardless of what command is run.
    """
    calls = []
    monkeypatch.setattr("valcore.tracing.configure_tracing", lambda cfg: calls.append(cfg))
    result = _invoke(runner, db_path, "version")
    assert result.exit_code == 0
    assert len(calls) == 1


def test_cli_group_configures_tracing_with_no_token_without_error(runner, db_path):
    """With no Logfire token configured, tracing configuration must still be a silent no-op."""
    result = _invoke(runner, db_path, "version")
    assert result.exit_code == 0
    assert result.exception is None


# -- list ---------------------------------------------------------------------


def test_list_evaluators_table(runner, store, db_path):
    result = _invoke(runner, db_path, "list", "evaluators")
    assert result.exit_code == 0
    assert "judge" in result.output
    assert "name" in result.output  # header present


def test_list_datasets_json(runner, store, db_path):
    result = _invoke(runner, db_path, "list", "datasets", "--json")
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed[0]["name"] == "cases"
    assert "id" in parsed[0]


def test_list_runs_json(runner, store, db_path):
    result = _invoke(runner, db_path, "list", "runs", "--json")
    assert result.exit_code == 0
    assert json.loads(result.output) == []


# -- resolve ------------------------------------------------------------------


def test_resolve_exact_name(store):
    assert resolve_evaluator(store, "judge").name == "judge"
    assert resolve_dataset(store, "cases").name == "cases"


def test_resolve_unique_prefix(store):
    evaluator = resolve_evaluator(store, "judge")
    assert resolve_evaluator(store, evaluator.id[:8]).id == evaluator.id


def test_resolve_ambiguous_prefix_lists_candidates(store):
    # Two evaluators whose ids share a leading prefix.
    a = store.create_evaluator("alpha")
    b = store.create_evaluator("beta")
    shared = "shared00" + "0" * 24
    with store.engine.connect() as conn:
        from sqlalchemy import text

        conn.execute(
            text("UPDATE evaluator SET id = :i WHERE id = :old"),
            {"i": shared + "a", "old": a.id},
        )
        conn.execute(
            text("UPDATE evaluator SET id = :i WHERE id = :old"),
            {"i": shared + "b", "old": b.id},
        )
        conn.commit()
    with pytest.raises(ContractError) as exc:
        resolve_evaluator(store, shared)
    assert "alpha" in str(exc.value)
    assert "beta" in str(exc.value)


def test_resolve_unknown_ref_names_it(store):
    with pytest.raises(NotFoundError) as exc:
        resolve_evaluator(store, "nonexistent-name")
    assert "nonexistent-name" in str(exc.value)


def test_resolve_short_prefix_rejected(store):
    # Not an exact name and shorter than the 4-char prefix minimum.
    with pytest.raises(NotFoundError):
        resolve_evaluator(store, "ab")


def test_resolve_version_none_returns_active(store):
    evaluator = resolve_evaluator(store, "judge")
    version = resolve_version(store, evaluator, None)
    assert version.id == evaluator.active_version_id


def test_resolve_version_none_errors_without_active(store):
    evaluator = store.create_evaluator("empty")
    with pytest.raises(NotFoundError):
        resolve_version(store, evaluator, None)


# -- run ----------------------------------------------------------------------


def test_run_happy_path_writes_results(runner, store, db_path, monkeypatch):
    monkeypatch.setattr("valcore.runner.build_agent", _constant_agent_builder("pass"))
    result = _invoke(runner, db_path, "run", "judge", "cases")
    assert result.exit_code == 0
    runs = store.list_runs()
    assert len(runs) == 1
    assert len(store.list_results(runs[0].id)) == 4


def test_run_json_stdout_is_pure_json(runner, store, db_path, monkeypatch):
    monkeypatch.setattr("valcore.runner.build_agent", _constant_agent_builder("pass"))
    result = _invoke(runner, db_path, "run", "judge", "cases", "--json")
    assert result.exit_code == 0
    # Progress went to stderr; stdout alone must parse as JSON and carry metrics.
    payload = json.loads(result.stdout)
    assert payload["metrics"]["n"] == 4
    assert "accuracy" in payload["metrics"]


def test_run_progress_goes_to_stderr(runner, store, db_path, monkeypatch):
    monkeypatch.setattr("valcore.runner.build_agent", _constant_agent_builder("pass"))
    result = _invoke(runner, db_path, "run", "judge", "cases", "--json")
    assert result.exit_code == 0
    assert "4/4" in result.stderr
    # stdout is clean JSON with no progress noise.
    json.loads(result.stdout)


def test_run_min_accuracy_above_exits_2(runner, store, db_path, monkeypatch):
    # Two "pass" labels of four rows: always-pass agent achieves accuracy 0.5.
    monkeypatch.setattr("valcore.runner.build_agent", _constant_agent_builder("pass"))
    result = _invoke(runner, db_path, "run", "judge", "cases", "--min-accuracy", "0.9")
    assert result.exit_code == 2
    assert "below" in result.stderr


def test_run_min_accuracy_below_exits_0(runner, store, db_path, monkeypatch):
    monkeypatch.setattr("valcore.runner.build_agent", _constant_agent_builder("pass"))
    result = _invoke(runner, db_path, "run", "judge", "cases", "--min-accuracy", "0.1")
    assert result.exit_code == 0


def test_run_unresolvable_evaluator_exits_1(runner, store, db_path):
    result = _invoke(runner, db_path, "run", "no-such-evaluator", "cases")
    assert result.exit_code == 1
    assert "error:" in result.stderr


# -- gateway guard --------------------------------------------------------------
#
# A keyless invocation must exit non-zero with a message naming `valcore config
# set-key`, and must persist no RunResult rows -- never the N-failed-rows outcome
# `require_gateway_key` exists to prevent (see config.require_gateway_key's docstring).


def test_run_no_gateway_key_exits_nonzero_naming_set_key(runner, store, db_path, monkeypatch):
    monkeypatch.delenv("PYDANTIC_AI_GATEWAY_API_KEY", raising=False)
    result = _invoke(runner, db_path, "run", "judge", "cases")
    assert result.exit_code != 0
    assert "valcore config set-key" in result.stderr
    for run in store.list_runs():
        assert store.list_results(run.id) == []


def test_experiment_no_gateway_key_exits_nonzero_naming_set_key(
    runner, store, db_path, monkeypatch
):
    monkeypatch.delenv("PYDANTIC_AI_GATEWAY_API_KEY", raising=False)
    result = _invoke(runner, db_path, "experiment", "judge", "cases")
    assert result.exit_code != 0
    assert "valcore config set-key" in result.stderr
    for run in store.list_runs():
        assert store.list_results(run.id) == []


def test_export_succeeds_without_gateway_key(runner, store, db_path, monkeypatch):
    monkeypatch.delenv("PYDANTIC_AI_GATEWAY_API_KEY", raising=False)
    result = _invoke(runner, db_path, "export", "judge")
    assert result.exit_code == 0


def test_list_succeeds_without_gateway_key(runner, store, db_path, monkeypatch):
    monkeypatch.delenv("PYDANTIC_AI_GATEWAY_API_KEY", raising=False)
    result = _invoke(runner, db_path, "list", "evaluators")
    assert result.exit_code == 0


def test_import_succeeds_without_gateway_key(runner, store, db_path, tmp_path, monkeypatch):
    monkeypatch.delenv("PYDANTIC_AI_GATEWAY_API_KEY", raising=False)
    out = tmp_path / "pkg.json"
    exported = _invoke(
        runner, db_path, "export", "judge", "--dataset", "cases", "--format", "json", "-o", str(out)
    )
    assert exported.exit_code == 0

    dest = tmp_path / "imported.db"
    result = _invoke(runner, dest, "import", str(out))
    assert result.exit_code == 0


# -- experiment -----------------------------------------------------------------


def test_experiment_happy_path_writes_results(runner, store, db_path, monkeypatch):
    monkeypatch.setattr(
        "valcore.experiment.build_agent", _constant_test_model_agent_builder("pass")
    )
    result = _invoke(runner, db_path, "experiment", "judge", "cases")
    assert result.exit_code == 0
    runs = store.list_runs()
    assert len(runs) == 1
    assert len(store.list_results(runs[0].id)) == 4


def test_experiment_json_stdout_is_pure_json(runner, store, db_path, monkeypatch):
    monkeypatch.setattr(
        "valcore.experiment.build_agent", _constant_test_model_agent_builder("pass")
    )
    result = _invoke(runner, db_path, "experiment", "judge", "cases", "--json")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["metrics"]["n"] == 4
    assert "accuracy" in payload["metrics"]


def test_experiment_concurrency_option_sets_run_concurrency(runner, store, db_path, monkeypatch):
    monkeypatch.setattr(
        "valcore.experiment.build_agent", _constant_test_model_agent_builder("pass")
    )
    result = _invoke(runner, db_path, "experiment", "judge", "cases", "--concurrency", "7")
    assert result.exit_code == 0
    assert store.list_runs()[0].concurrency == 7


def test_experiment_unresolvable_evaluator_exits_1(runner, store, db_path):
    result = _invoke(runner, db_path, "experiment", "no-such-evaluator", "cases")
    assert result.exit_code == 1
    assert "error:" in result.stderr


def test_experiment_has_no_watch_option(runner, store, db_path, monkeypatch):
    """``Dataset.evaluate`` cannot be cancelled, so ``experiment`` must not gain ``--watch``."""
    monkeypatch.setattr(
        "valcore.experiment.build_agent", _constant_test_model_agent_builder("pass")
    )
    result = _invoke(runner, db_path, "experiment", "judge", "cases", "--watch")
    assert result.exit_code != 0


def test_experiment_and_run_agree(runner, store, db_path, monkeypatch):
    """The CLI-level version of the two-engines-agree guarantee: identical metrics."""
    monkeypatch.setattr("valcore.runner.build_agent", _constant_test_model_agent_builder("pass"))
    monkeypatch.setattr(
        "valcore.experiment.build_agent", _constant_test_model_agent_builder("pass")
    )

    run_result = _invoke(runner, db_path, "run", "judge", "cases", "--json")
    assert run_result.exit_code == 0
    run_payload = json.loads(run_result.stdout)

    experiment_result = _invoke(runner, db_path, "experiment", "judge", "cases", "--json")
    assert experiment_result.exit_code == 0
    experiment_payload = json.loads(experiment_result.stdout)

    assert experiment_payload["status"] == "completed"
    assert experiment_payload["metrics"] == run_payload["metrics"]


def test_experiment_json_payload_shape_matches_run(runner, store, db_path, monkeypatch):
    monkeypatch.setattr("valcore.runner.build_agent", _constant_test_model_agent_builder("pass"))
    monkeypatch.setattr(
        "valcore.experiment.build_agent", _constant_test_model_agent_builder("pass")
    )

    run_payload = json.loads(_invoke(runner, db_path, "run", "judge", "cases", "--json").stdout)
    experiment_payload = json.loads(
        _invoke(runner, db_path, "experiment", "judge", "cases", "--json").stdout
    )

    assert set(experiment_payload.keys()) == set(run_payload.keys())
    assert set(experiment_payload["results"][0].keys()) == set(run_payload["results"][0].keys())


# -- export -------------------------------------------------------------------


def test_export_to_file_is_compilable(runner, store, db_path, tmp_path):
    out = tmp_path / "script.py"
    result = _invoke(runner, db_path, "export", "judge", "-o", str(out))
    assert result.exit_code == 0
    compile(out.read_text(), str(out), "exec")


def test_export_to_stdout(runner, store, db_path):
    result = _invoke(runner, db_path, "export", "judge")
    assert result.exit_code == 0
    assert "class" in result.output
    compile(result.output, "<stdout>", "exec")


def test_export_code_default_is_byte_identical_to_render_script(runner, store, db_path):
    # Locked decision 6: `export <evaluator>` with no new flags must keep emitting exactly the
    # Python script it emits today, byte-for-byte. render_script is the source of truth.
    ev = resolve_evaluator(store, "judge")
    ver = resolve_version(store, ev, None)
    expected = render_script(ver)

    result = _invoke(runner, db_path, "export", "judge")
    assert result.exit_code == 0
    assert result.stdout == expected


def test_export_evaluator_json_bundled_is_parseable(runner, store, db_path, tmp_path):
    out = tmp_path / "pkg.json"
    result = _invoke(runner, db_path, "export", "judge", "--format", "json", "-o", str(out))
    assert result.exit_code == 0
    assert out.exists()

    pkg = EvalPackage.from_text(out.read_text())
    assert pkg.spec is not None
    assert pkg.valcore is not None
    # A JSON export that includes an evaluator writes the companion module beside the config.
    assert (tmp_path / "valcore_judge.py").exists()


def test_export_dataset_only_json_has_no_agent(runner, store, db_path, tmp_path):
    out = tmp_path / "ds.json"
    result = _invoke(
        runner, db_path, "export", "--dataset", "cases", "--format", "json", "-o", str(out)
    )
    assert result.exit_code == 0

    pkg = EvalPackage.from_text(out.read_text())
    assert pkg.spec is None
    assert pkg.dataset is not None
    # No evaluator means no companion module.
    assert not (tmp_path / "valcore_judge.py").exists()


def test_export_dataset_code_emits_dataset_module(runner, store, db_path, tmp_path):
    out = tmp_path / "ds_module.py"
    result = _invoke(
        runner, db_path, "export", "--dataset", "cases", "--format", "code", "-o", str(out)
    )
    assert result.exit_code == 0
    source = out.read_text()
    assert "pydantic_evals" in source
    compile(source, str(out), "exec")


def test_export_neither_evaluator_nor_dataset_exits_nonzero(runner, store, db_path):
    result = _invoke(runner, db_path, "export")
    assert result.exit_code != 0


def test_export_split_writes_two_json_files_plus_judge(runner, store, db_path, tmp_path):
    out = tmp_path / "pkg.json"
    result = _invoke(
        runner,
        db_path,
        "export",
        "judge",
        "--dataset",
        "cases",
        "--format",
        "json",
        "--split",
        "-o",
        str(out),
    )
    assert result.exit_code == 0
    assert (tmp_path / "pkg.agent.json").exists()
    assert (tmp_path / "pkg.dataset.json").exists()
    assert (tmp_path / "valcore_judge.py").exists()
    # Split emits exactly the two hoisted halves; the bundled name is never written.
    assert sorted(p.name for p in tmp_path.glob("*.json")) == ["pkg.agent.json", "pkg.dataset.json"]


def test_export_split_without_output_exits_nonzero(runner, store, db_path):
    result = _invoke(runner, db_path, "export", "judge", "--format", "json", "--split")
    assert result.exit_code != 0


def test_export_split_with_code_format_exits_nonzero(runner, store, db_path, tmp_path):
    out = tmp_path / "x.py"
    result = _invoke(
        runner, db_path, "export", "judge", "--format", "code", "--split", "-o", str(out)
    )
    assert result.exit_code != 0


def test_export_code_both_entities_without_output_exits_nonzero(runner, store, db_path):
    # Two files cannot share stdout.
    result = _invoke(runner, db_path, "export", "judge", "--dataset", "cases", "--format", "code")
    assert result.exit_code != 0


def test_export_json_evaluator_stdout_notes_omitted_companion(runner, store, db_path):
    result = _invoke(runner, db_path, "export", "judge", "--format", "json")
    assert result.exit_code == 0
    # stdout is the JSON package alone.
    pkg = EvalPackage.from_text(result.stdout)
    assert pkg.spec is not None
    # The companion module has nowhere to go without -o; its omission is noted on stderr.
    assert "valcore_judge.py" in result.stderr


def test_export_json_refuses_to_clobber_derived_companion(runner, store, db_path, tmp_path):
    # Locked decision: the -o path may be overwritten (the user chose it), but a sibling file a
    # multi-file export derives — here the companion module — must never silently replace an
    # existing file the user did not name.
    out = tmp_path / "pkg.json"
    existing = tmp_path / "valcore_judge.py"
    existing.write_text("# precious, do not clobber\n")

    result = _invoke(runner, db_path, "export", "judge", "--format", "json", "-o", str(out))
    assert result.exit_code != 0
    # The pre-existing sibling is left exactly as it was.
    assert existing.read_text() == "# precious, do not clobber\n"


def test_export_json_overwrites_the_named_output(runner, store, db_path, tmp_path):
    # The -o target itself is fair game: the user named it, so a stale file there is replaced.
    out = tmp_path / "pkg.json"
    out.write_text("stale")

    result = _invoke(runner, db_path, "export", "judge", "--format", "json", "-o", str(out))
    assert result.exit_code == 0
    pkg = EvalPackage.from_text(out.read_text())
    assert pkg.spec is not None


# -- import -------------------------------------------------------------------


def _fresh_store(db_path) -> Store:
    """Open a Store on a (possibly import-created) db for post-import inspection."""
    engine = create_engine(db_path)
    init_db(engine)
    return Store(engine)


def test_import_bundled_round_trips(runner, store, db_path, tmp_path):
    out = tmp_path / "pkg.json"
    exported = _invoke(
        runner, db_path, "export", "judge", "--dataset", "cases", "--format", "json", "-o", str(out)
    )
    assert exported.exit_code == 0

    dest = tmp_path / "imported.db"
    imported = _invoke(runner, dest, "import", str(out))
    assert imported.exit_code == 0

    s = _fresh_store(dest)

    datasets = s.list_datasets()
    assert len(datasets) == 1
    ds = datasets[0]
    assert ds.name == "cases"
    rows = s.list_rows(ds.id)
    assert [r.data for r in rows] == [{"input": f"in{i}", "output": f"out{i}"} for i in range(4)]
    assert [r.label["value"] for r in rows] == ["pass", "fail", "pass", "fail"]

    evaluators = s.list_evaluators()
    assert len(evaluators) == 1
    ev = evaluators[0]
    assert ev.active_version_id is not None
    version = s.get_version(ev.active_version_id)
    assert version.score_field == "verdict"
    assert version.score_kind is ScoreKind.CATEGORICAL
    assert version.score_labels == ["pass", "fail"]
    # output_fields round-trips through the JSON Schema encoding losslessly.
    expected_fields = [OutputField.model_validate(f) for f in VERSION_FIELDS["output_fields"]]
    assert parse_output_fields(version) == expected_fields


def test_import_name_override(runner, store, db_path, tmp_path):
    out = tmp_path / "pkg.json"
    _invoke(runner, db_path, "export", "--dataset", "cases", "--format", "json", "-o", str(out))

    dest = tmp_path / "imported.db"
    result = _invoke(runner, dest, "import", str(out), "--name", "renamed-cases")
    assert result.exit_code == 0

    s = _fresh_store(dest)
    assert [d.name for d in s.list_datasets()] == ["renamed-cases"]


def test_import_invalid_agent_persists_nothing(runner, store, db_path, tmp_path):
    out = tmp_path / "pkg.json"
    _invoke(
        runner, db_path, "export", "judge", "--dataset", "cases", "--format", "json", "-o", str(out)
    )

    # Break the agent so validate_version rejects it: a model string with no gateway route.
    doc = json.loads(out.read_text())
    doc["agent"]["model"] = "bogus-no-gateway-prefix"
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(doc))

    dest = tmp_path / "dest.db"
    result = _invoke(runner, dest, "import", str(broken))
    assert result.exit_code != 0

    # Validation happens before any persistence, so nothing is created.
    s = _fresh_store(dest)
    assert s.list_evaluators() == []
    assert s.list_datasets() == []


def test_import_py_path_exits_nonzero(runner, db_path, tmp_path):
    script = tmp_path / "thing.py"
    script.write_text("print('hi')\n")
    result = _invoke(runner, db_path, "import", str(script))
    assert result.exit_code != 0
    assert "json" in result.stderr.lower()


# -- config -------------------------------------------------------------------


def test_config_set_key_then_get_masks(runner, db_path):
    set_result = _invoke(runner, db_path, "config", "set-key", "sk-secret-1234")
    assert set_result.exit_code == 0

    get_result = _invoke(runner, db_path, "config", "get")
    assert get_result.exit_code == 0
    assert "sk-…1234" in get_result.output
    assert "sk-secret-1234" not in get_result.output


def test_config_get_show_key_reveals(runner, db_path):
    _invoke(runner, db_path, "config", "set-key", "sk-secret-1234")
    result = _invoke(runner, db_path, "config", "get", "--show-key")
    assert result.exit_code == 0
    assert "sk-secret-1234" in result.output


def test_config_set_logfire_token_persists_and_preserves_others(runner, db_path):
    _invoke(runner, db_path, "config", "set-key", "sk-secret-1234")
    result = _invoke(runner, db_path, "config", "set-logfire-token", "lf-token-5678")
    assert result.exit_code == 0

    cfg = load_config()
    assert cfg.logfire_token == "lf-token-5678"
    assert cfg.gateway_api_key == "sk-secret-1234"


def test_config_set_logfire_token_prompts_when_omitted(runner, db_path):
    result = _invoke(runner, db_path, "config", "set-logfire-token", input="lf-prompted\n")
    assert result.exit_code == 0
    assert load_config().logfire_token == "lf-prompted"
    # Hidden input: the typed value never echoes to output.
    assert "lf-prompted" not in result.output


def test_config_set_logfire_key_persists_and_preserves_others(runner, db_path):
    _invoke(runner, db_path, "config", "set-logfire-token", "lf-existing-token")
    result = _invoke(runner, db_path, "config", "set-logfire-key", "lf-key-9999")
    assert result.exit_code == 0

    cfg = load_config()
    assert cfg.logfire_api_key == "lf-key-9999"
    assert cfg.logfire_token == "lf-existing-token"


def test_config_set_logfire_key_prompts_when_omitted(runner, db_path):
    result = _invoke(runner, db_path, "config", "set-logfire-key", input="lf-key-prompted\n")
    assert result.exit_code == 0
    assert load_config().logfire_api_key == "lf-key-prompted"
    assert "lf-key-prompted" not in result.output


def test_config_get_logfire_presence_changes_when_set_and_never_leaks_values(runner, db_path):
    before = json.loads(_invoke(runner, db_path, "config", "get", "--json").output)

    _invoke(runner, db_path, "config", "set-logfire-token", "lf-secret-token")
    _invoke(runner, db_path, "config", "set-logfire-key", "lf-secret-apikey")

    after_result = _invoke(runner, db_path, "config", "get", "--json")
    assert after_result.exit_code == 0
    after = json.loads(after_result.output)

    # Presence is reflected somehow -- as a masked string or boolean, format-agnostic here --
    # but the raw secret is never the field's value, and never appears anywhere in output.
    assert after["logfire_token"] != before["logfire_token"]
    assert after["logfire_api_key"] != before["logfire_api_key"]
    assert after["logfire_token"] != "lf-secret-token"
    assert after["logfire_api_key"] != "lf-secret-apikey"
    assert "lf-secret-token" not in after_result.output
    assert "lf-secret-apikey" not in after_result.output


def test_config_get_reports_effective_presence_from_env_only(runner, db_path, monkeypatch):
    """An env-only key or token, never written to the config file, must report as present.

    ``gateway_key_present``/``logfire_token_present`` treat an exported env var as
    effectively set, matching ``apply_gateway_key``'s env-wins precedence -- ``config get``
    must agree rather than fall back to the raw (``None``) file value and report a false
    absence.
    """
    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_API_KEY", "sk-env-only-1234")
    monkeypatch.setenv("LOGFIRE_TOKEN", "lf-env-only-token")

    result = _invoke(runner, db_path, "config", "get", "--json")
    assert result.exit_code == 0
    payload = json.loads(result.output)

    assert payload["gateway_api_key"] not in (None, False)
    assert payload["logfire_token"] is True
    assert load_config().gateway_api_key is None
    assert load_config().logfire_token is None
    assert "sk-env-only-1234" not in result.output
    assert "lf-env-only-token" not in result.output


def test_config_get_show_key_does_not_reveal_logfire_secrets(runner, db_path):
    """``--show-key`` already governs revealing the gateway key; it must not newly govern these."""
    _invoke(runner, db_path, "config", "set-key", "sk-secret-1234")
    _invoke(runner, db_path, "config", "set-logfire-token", "lf-secret-token")
    _invoke(runner, db_path, "config", "set-logfire-key", "lf-secret-apikey")

    result = _invoke(runner, db_path, "config", "get", "--show-key")
    assert result.exit_code == 0
    assert "sk-secret-1234" in result.output
    assert "lf-secret-token" not in result.output
    assert "lf-secret-apikey" not in result.output


# -- logfire --------------------------------------------------------------------


def test_logfire_push_prints_id_name_and_case_count_never_a_url(
    runner, store, db_path, monkeypatch
):
    async def fake_push_dataset(
        dataset, rows, *, api_key=None, name=None, description=None, on_conflict="update"
    ):
        return {
            "id": "abc-123",
            "name": "pushed-cases",
            "case_count": 4,
            "output_schema": {"type": "string"},
        }

    monkeypatch.setattr("valcore.logfire_io.push_dataset", fake_push_dataset)
    result = _invoke(runner, db_path, "logfire", "push", "cases")
    assert result.exit_code == 0
    assert "abc-123" in result.output
    assert "pushed-cases" in result.output
    assert "4" in result.output
    assert "http" not in result.output.lower()
    assert "url" not in result.output.lower()


def test_logfire_push_resolves_dataset_and_passes_its_rows(runner, store, db_path, monkeypatch):
    captured = {}

    async def fake_push_dataset(
        dataset, rows, *, api_key=None, name=None, description=None, on_conflict="update"
    ):
        captured["dataset_name"] = dataset.name
        captured["row_count"] = len(rows)
        return {"id": "x", "name": dataset.name, "case_count": len(rows), "output_schema": None}

    monkeypatch.setattr("valcore.logfire_io.push_dataset", fake_push_dataset)
    result = _invoke(runner, db_path, "logfire", "push", "cases")
    assert result.exit_code == 0
    assert captured == {"dataset_name": "cases", "row_count": 4}


def test_logfire_push_defaults_have_no_name_or_description(runner, store, db_path, monkeypatch):
    calls = {}

    async def fake_push_dataset(
        dataset, rows, *, api_key=None, name=None, description=None, on_conflict="update"
    ):
        calls.update(name=name, description=description, on_conflict=on_conflict)
        return {"id": "x", "name": "cases", "case_count": len(rows), "output_schema": None}

    monkeypatch.setattr("valcore.logfire_io.push_dataset", fake_push_dataset)
    result = _invoke(runner, db_path, "logfire", "push", "cases")
    assert result.exit_code == 0
    assert calls == {"name": None, "description": None, "on_conflict": "update"}


def test_logfire_push_passes_name_description_and_on_conflict_through(
    runner, store, db_path, monkeypatch
):
    calls = {}

    async def fake_push_dataset(
        dataset, rows, *, api_key=None, name=None, description=None, on_conflict="update"
    ):
        calls.update(name=name, description=description, on_conflict=on_conflict)
        return {"id": "x", "name": name, "case_count": len(rows), "output_schema": None}

    monkeypatch.setattr("valcore.logfire_io.push_dataset", fake_push_dataset)
    result = _invoke(
        runner,
        db_path,
        "logfire",
        "push",
        "cases",
        "--name",
        "custom-name",
        "--description",
        "custom description",
        "--on-conflict",
        "error",
    )
    assert result.exit_code == 0
    assert calls == {
        "name": "custom-name",
        "description": "custom description",
        "on_conflict": "error",
    }


def test_logfire_push_invalid_on_conflict_choice_exits_nonzero(runner, store, db_path):
    result = _invoke(runner, db_path, "logfire", "push", "cases", "--on-conflict", "bogus")
    assert result.exit_code != 0


def test_logfire_push_unresolvable_dataset_exits_1(runner, store, db_path):
    result = _invoke(runner, db_path, "logfire", "push", "no-such-dataset")
    assert result.exit_code == 1
    assert "error:" in result.stderr


def test_logfire_push_no_api_key_exits_nonzero_naming_set_logfire_key(runner, store, db_path):
    # No stub installed: with no key configured, `push_dataset` must fail before any
    # network-facing import or call, exactly as `test_logfire_io.py` pins directly.
    result = _invoke(runner, db_path, "logfire", "push", "cases")
    assert result.exit_code != 0
    assert "valcore config set-logfire-key" in result.stderr


# -- ./valcore.db startup notice --------------------------------------------


def test_local_db_notice_appears_and_leaves_file(runner, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    local = tmp_path / "valcore.db"
    local.write_bytes(b"legacy sqlite bytes")
    resolved = tmp_path / "elsewhere.db"

    result = runner.invoke(cli, ["--db", str(resolved), "list", "evaluators"])
    assert result.exit_code == 0
    assert "valcore.db" in result.stderr
    # The file is never moved or copied.
    assert local.read_bytes() == b"legacy sqlite bytes"


def test_no_notice_when_local_db_absent(runner, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resolved = tmp_path / "elsewhere.db"
    result = runner.invoke(cli, ["--db", str(resolved), "list", "evaluators"])
    assert result.exit_code == 0
    assert "valcore.db exists" not in result.stderr


# -- error handling -----------------------------------------------------------


def test_domain_error_printed_and_exits_1(runner, store, db_path):
    result = _invoke(runner, db_path, "export", "no-such-evaluator")
    assert result.exit_code == 1
    assert result.stderr.startswith("error:")


# -- serve --------------------------------------------------------------------


def test_serve_calls_uvicorn_with_host_and_port(runner, db_path, monkeypatch):
    calls = {}

    def fake_run(app, host, port):
        calls["host"] = host
        calls["port"] = port

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    result = _invoke(
        runner, db_path, "serve", "--port", "9123", "--host", "0.0.0.0", "--no-browser"
    )
    assert result.exit_code == 0
    assert calls == {"host": "0.0.0.0", "port": 9123}
