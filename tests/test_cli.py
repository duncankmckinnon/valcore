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

import gc
import json
import warnings
from collections.abc import Callable, Iterator
from importlib.metadata import version as package_version

import pytest
from click.testing import CliRunner
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from sqlalchemy.engine import Engine

from valcore.cli.main import cli
from valcore.cli.resolve import (
    resolve_dataset,
    resolve_derivation,
    resolve_evaluator,
    resolve_version,
)
from valcore.config import load_config
from valcore.config_io import EvalPackage
from valcore.errors import ContractError, NotFoundError
from valcore.export import render_script
from valcore.factory import build_output_model
from valcore.models import DerivationRole, LabelSource, OutputField, ScoreKind, parse_output_fields
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
def store(db_path) -> Iterator[Store]:
    """A real Store backed by the fixture db path, with an evaluator and dataset seeded."""
    engine = create_engine(db_path)
    init_db(engine)
    store = Store(engine)
    _seed(store, labels=["pass", "fail", "pass", "fail"])
    try:
        yield store
    finally:
        engine.dispose()


def _seed(store: Store, labels: list[str | None]) -> None:
    """Seed one evaluator (active version) and one dataset with the given row labels.

    Ground truth lives on a ``LabelSet``/``Annotation`` pair now, not the legacy
    ``DatasetRow.label`` column -- mirroring ``make_dataset`` in ``test_api_runs.py``.
    """
    evaluator = store.create_evaluator("judge", description="a judge")
    store.create_version(evaluator.id, **VERSION_FIELDS)
    dataset = store.create_dataset("cases", "", ["input", "output"])
    rows = store.add_rows(
        dataset.id, [{"input": f"in{i}", "output": f"out{i}"} for i in range(len(labels))]
    )
    label_set = store.create_label_set(
        dataset.id,
        "quality",
        "",
        ScoreKind.CATEGORICAL,
        labels=[{"name": "pass", "description": ""}, {"name": "fail", "description": ""}],
    )
    for row, label in zip(rows, labels, strict=True):
        if label is not None:
            store.set_annotation(label_set.id, row.id, labels=[label], source=LabelSource.MANUAL)


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


def test_invocation_leaves_no_open_database_connection(runner: CliRunner, tmp_path) -> None:
    """Every command opens its own engine, so an undisposed one keeps a SQLite
    connection alive until the garbage collector finalises it -- which surfaces as a
    ResourceWarning charged to whichever unrelated test happens to be running then.
    """
    db_path = tmp_path / "dispose.db"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("ignore")
        warnings.filterwarnings(
            "always",
            "unclosed database in <sqlite3.Connection object at",
            ResourceWarning,
        )
        result = _invoke(runner, db_path, "list", "evaluators")
        gc.collect()
    assert result.exit_code == 0
    assert [str(w.message) for w in caught] == []


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


def test_list_derivations_renders_saved_overlay_fields(runner, store, db_path):
    """The generic listing exposes the identity and shape of accepted derivations."""
    dataset = resolve_dataset(store, "cases")
    agent = store.create_agent("writer")
    version = store.create_agent_version(
        agent.id,
        version_name="v1",
        model="local/codex",
        spec={"model": "test"},
        prompt_template="{input}",
        required_columns=["input"],
        deps_mapping={},
    )
    row = store.list_rows(dataset.id)[0]
    store.save_derivation(
        dataset_id=dataset.id,
        agent_version_id=version.id,
        response_columns=["draft"],
        responses=[{"row_id": row.id, "data": {"draft": "answer"}}],
    )

    result = _invoke(runner, db_path, "list", "derivations")

    assert result.exit_code == 0, result.stderr
    for column in ("agent", "version", "ordinal", "rows", "response_columns"):
        assert column in result.output


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


def test_resolve_derivation_accepts_a_saved_derivation_reference(store):
    """Derivation references combine agent, version, and save-time ordinal."""
    dataset = resolve_dataset(store, "cases")
    agent = store.create_agent("writer")
    version = store.create_agent_version(
        agent.id,
        version_name="v1",
        model="local/codex",
        spec={"model": "test"},
        prompt_template="{input}",
        required_columns=["input"],
        deps_mapping={},
    )
    row = store.list_rows(dataset.id)[0]
    derivation = store.save_derivation(
        dataset_id=dataset.id,
        agent_version_id=version.id,
        response_columns=["draft"],
        responses=[{"row_id": row.id, "data": {"draft": "answer"}}],
    )

    assert resolve_derivation(store, "writer/v1/0", dataset_id=dataset.id).id == derivation.id
    assert resolve_derivation(store, derivation.id[:8], dataset_id=dataset.id).id == derivation.id


def test_resolve_derivation_canonical_ambiguity_lists_candidate_ids(store):
    """Canonical ordinals can collide across datasets, so ambiguity names every overlay."""
    agent = store.create_agent("writer")
    version = store.create_agent_version(
        agent.id,
        version_name="v1",
        model="local/codex",
        spec={"model": "test"},
        prompt_template="{input}",
        required_columns=["input"],
        deps_mapping={},
    )
    derivations = []
    for name in ("first", "second"):
        dataset = store.create_dataset(name, "", ["input"])
        row = store.add_rows(dataset.id, [{"input": name}])[0]
        derivations.append(
            store.save_derivation(
                dataset_id=dataset.id,
                agent_version_id=version.id,
                response_columns=["draft"],
                responses=[{"row_id": row.id, "data": {"draft": name}}],
            )
        )

    with pytest.raises(ContractError) as exc_info:
        resolve_derivation(store, "writer/v1/0")

    message = str(exc_info.value)
    assert all(derivation.id[:8] in message for derivation in derivations)


def test_resolve_derivation_missing_canonical_ref_names_it(store):
    """A missing canonical derivation reports the complete requested reference."""
    with pytest.raises(NotFoundError, match="missing/v1/0"):
        resolve_derivation(store, "missing/v1/0")


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
    result = _invoke(runner, db_path, "run", "evaluator", "judge", "--dataset", "cases")
    assert result.exit_code == 0
    runs = store.list_runs()
    assert len(runs) == 1
    assert len(store.list_results(runs[0].id)) == 4


def test_run_json_stdout_is_pure_json(runner, store, db_path, monkeypatch):
    monkeypatch.setattr("valcore.runner.build_agent", _constant_agent_builder("pass"))
    result = _invoke(runner, db_path, "run", "evaluator", "judge", "--dataset", "cases", "--json")
    assert result.exit_code == 0
    # Progress went to stderr; stdout alone must parse as JSON and carry metrics.
    payload = json.loads(result.stdout)
    assert payload["metrics"]["n"] == 4
    assert "accuracy" in payload["metrics"]


def test_run_progress_goes_to_stderr(runner, store, db_path, monkeypatch):
    monkeypatch.setattr("valcore.runner.build_agent", _constant_agent_builder("pass"))
    result = _invoke(runner, db_path, "run", "evaluator", "judge", "--dataset", "cases", "--json")
    assert result.exit_code == 0
    assert "4/4" in result.stderr
    # stdout is clean JSON with no progress noise.
    json.loads(result.stdout)


def test_run_min_accuracy_above_exits_2(runner, store, db_path, monkeypatch):
    # Two "pass" labels of four rows: always-pass agent achieves accuracy 0.5.
    monkeypatch.setattr("valcore.runner.build_agent", _constant_agent_builder("pass"))
    result = _invoke(
        runner, db_path, "run", "evaluator", "judge", "--dataset", "cases", "--min-accuracy", "0.9"
    )
    assert result.exit_code == 2
    assert "below" in result.stderr


def test_run_min_accuracy_below_exits_0(runner, store, db_path, monkeypatch):
    monkeypatch.setattr("valcore.runner.build_agent", _constant_agent_builder("pass"))
    result = _invoke(
        runner, db_path, "run", "evaluator", "judge", "--dataset", "cases", "--min-accuracy", "0.1"
    )
    assert result.exit_code == 0


def test_run_unresolvable_evaluator_exits_1(runner, store, db_path):
    result = _invoke(runner, db_path, "run", "evaluator", "no-such-evaluator", "--dataset", "cases")
    assert result.exit_code == 1
    assert "error:" in result.stderr


def test_run_evaluator_derivation_scores_its_usable_response_union(
    runner, store, db_path, monkeypatch
):
    """An evaluator reads the base row plus a saved response overlay through ``--derivation``."""
    monkeypatch.setattr("valcore.runner.build_agent", _constant_agent_builder("pass"))
    dataset = resolve_dataset(store, "cases")
    agent = store.create_agent("writer")
    agent_version = store.create_agent_version(
        agent.id,
        version_name="v1",
        model="local/codex",
        spec={"model": "test"},
        prompt_template="{input}",
        required_columns=["input"],
        deps_mapping={},
    )
    rows = store.list_rows(dataset.id)
    derivation = store.save_derivation(
        dataset_id=dataset.id,
        agent_version_id=agent_version.id,
        response_columns=["draft"],
        responses=[{"row_id": row.id, "data": {"draft": f"draft-{row.idx}"}} for row in rows],
    )
    evaluator = resolve_evaluator(store, "judge")
    store.create_version(
        evaluator.id,
        **{
            **VERSION_FIELDS,
            "version_name": "reads-draft",
            "prompt_template": "Draft: {draft}",
            "required_columns": ["draft"],
        },
    )

    result = _invoke(
        runner,
        db_path,
        "run",
        "evaluator",
        "judge",
        "--dataset",
        "cases",
        "--version",
        "reads-draft",
        "--kind",
        "eval",
        "--derivation",
        derivation.id[:8],
    )

    assert result.exit_code == 0, result.output + result.stderr
    run = store.list_runs()[0]
    assert store.get_run_derivation(run.id).role is DerivationRole.READS
    assert len(store.list_results(run.id)) == len(rows)


def test_run_evaluator_rejects_derivation_validation_without_label_sets(runner, store, db_path):
    """Validation labels apply to the base contract, not a derivation-only response schema."""
    dataset = resolve_dataset(store, "cases")
    agent = store.create_agent("writer")
    version = store.create_agent_version(
        agent.id,
        version_name="v1",
        model="local/codex",
        spec={"model": "test"},
        prompt_template="{input}",
        required_columns=["input"],
        deps_mapping={},
    )
    row = store.list_rows(dataset.id)[0]
    derivation = store.save_derivation(
        dataset_id=dataset.id,
        agent_version_id=version.id,
        response_columns=["draft"],
        responses=[{"row_id": row.id, "data": {"draft": "answer"}}],
    )

    result = _invoke(
        runner,
        db_path,
        "run",
        "evaluator",
        "judge",
        "--dataset",
        "cases",
        "--derivation",
        derivation.id[:8],
        "--kind",
        "validation",
    )

    assert result.exit_code == 1
    assert "label set" in result.stderr.lower()


def test_run_evaluator_rejects_derivation_validation_before_gateway_key_check(
    runner, store, db_path, monkeypatch
):
    """An invalid command contract is diagnosed even on a keyless installation."""
    monkeypatch.delenv("PYDANTIC_AI_GATEWAY_API_KEY", raising=False)

    result = _invoke(
        runner,
        db_path,
        "run",
        "evaluator",
        "judge",
        "--dataset",
        "cases",
        "--derivation",
        "missing",
        "--kind",
        "validation",
    )

    assert result.exit_code == 1
    assert "label set" in result.stderr.lower()
    assert "gateway" not in result.stderr.lower()


def test_run_evaluator_rejects_derive_kind_as_a_click_choice(runner, store, db_path):
    """Derive runs belong to agent versions and are not an evaluator mode."""
    result = _invoke(
        runner,
        db_path,
        "run",
        "evaluator",
        "judge",
        "--dataset",
        "cases",
        "--kind",
        "derive",
    )

    assert result.exit_code == 2
    assert "invalid value for '--kind'" in result.stderr.lower()


@pytest.mark.parametrize(
    "args",
    [("run", "judge", "cases"), ("experiment", "judge", "cases")],
)
def test_removed_run_spellings_exit_nonzero(runner, store, db_path, args):
    """The old command spellings are deliberately a hard break."""
    result = _invoke(runner, db_path, *args)

    assert result.exit_code != 0


# -- gateway guard --------------------------------------------------------------
#
# A keyless invocation must exit non-zero with a message naming `valcore config
# set-key`, and must persist no RunResult rows -- never the N-failed-rows outcome
# `require_gateway_key` exists to prevent (see config.require_gateway_key's docstring).


def test_run_no_gateway_key_exits_nonzero_naming_set_key(runner, store, db_path, monkeypatch):
    monkeypatch.delenv("PYDANTIC_AI_GATEWAY_API_KEY", raising=False)
    result = _invoke(runner, db_path, "run", "evaluator", "judge", "--dataset", "cases")
    assert result.exit_code != 0
    assert "valcore config set-key" in result.stderr
    for run in store.list_runs():
        assert store.list_results(run.id) == []


def test_experiment_no_gateway_key_exits_nonzero_naming_set_key(
    runner, store, db_path, monkeypatch
):
    monkeypatch.delenv("PYDANTIC_AI_GATEWAY_API_KEY", raising=False)
    result = _invoke(runner, db_path, "run", "experiment", "judge", "--dataset", "cases")
    assert result.exit_code != 0
    assert "valcore config set-key" in result.stderr
    for run in store.list_runs():
        assert store.list_results(run.id) == []


# A local/<cli>:<name> version reaches an already-logged-in CLI on this machine, never the
# gateway, so the guard must not fire for one -- a keyless machine is the exact scenario the
# local-CLI feature exists for. The guard is decided by the resolved version's own model, so
# the version must be resolved before the check, not after.


def _local_model_version(store: Store) -> None:
    """Add a ``local/claude`` version ``v2`` to the seeded evaluator.

    Selected explicitly with ``--version v2``; the seeded gateway version stays active so
    the two coexist exactly as they would in a real store.
    """
    evaluator = resolve_evaluator(store, "judge")
    store.create_version(
        evaluator.id, **{**VERSION_FIELDS, "version_name": "v2", "model": "local/claude"}
    )


def test_run_with_local_model_succeeds_without_gateway_key(runner, store, db_path, monkeypatch):
    monkeypatch.delenv("PYDANTIC_AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.setattr("valcore.runner.build_agent", _constant_agent_builder("pass"))
    _local_model_version(store)

    result = _invoke(
        runner, db_path, "run", "evaluator", "judge", "--dataset", "cases", "--version", "v2"
    )

    assert result.exit_code == 0, result.output + result.stderr
    runs = store.list_runs()
    assert runs and len(store.list_results(runs[0].id)) == 4


def test_experiment_with_local_model_succeeds_without_gateway_key(
    runner, store, db_path, monkeypatch
):
    monkeypatch.delenv("PYDANTIC_AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.setattr(
        "valcore.experiment.build_agent", _constant_test_model_agent_builder("pass")
    )
    _local_model_version(store)

    result = _invoke(
        runner, db_path, "run", "experiment", "judge", "--dataset", "cases", "--version", "v2"
    )

    assert result.exit_code == 0, result.output + result.stderr


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
    result = _invoke(runner, db_path, "run", "experiment", "judge", "--dataset", "cases")
    assert result.exit_code == 0
    runs = store.list_runs()
    assert len(runs) == 1
    assert len(store.list_results(runs[0].id)) == 4


def test_experiment_json_stdout_is_pure_json(runner, store, db_path, monkeypatch):
    monkeypatch.setattr(
        "valcore.experiment.build_agent", _constant_test_model_agent_builder("pass")
    )
    result = _invoke(runner, db_path, "run", "experiment", "judge", "--dataset", "cases", "--json")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["metrics"]["n"] == 4
    assert "accuracy" in payload["metrics"]


def test_experiment_concurrency_option_sets_run_concurrency(runner, store, db_path, monkeypatch):
    monkeypatch.setattr(
        "valcore.experiment.build_agent", _constant_test_model_agent_builder("pass")
    )
    result = _invoke(
        runner, db_path, "run", "experiment", "judge", "--dataset", "cases", "--concurrency", "7"
    )
    assert result.exit_code == 0
    assert store.list_runs()[0].concurrency == 7


def test_experiment_unresolvable_evaluator_exits_1(runner, store, db_path):
    result = _invoke(
        runner, db_path, "run", "experiment", "no-such-evaluator", "--dataset", "cases"
    )
    assert result.exit_code == 1
    assert "error:" in result.stderr


def test_experiment_has_no_watch_option(runner, store, db_path, monkeypatch):
    """``Dataset.evaluate`` cannot be cancelled, so ``experiment`` must not gain ``--watch``."""
    monkeypatch.setattr(
        "valcore.experiment.build_agent", _constant_test_model_agent_builder("pass")
    )
    result = _invoke(runner, db_path, "run", "experiment", "judge", "--dataset", "cases", "--watch")
    assert result.exit_code != 0


def test_experiment_and_run_agree(runner, store, db_path, monkeypatch):
    """The CLI-level version of the two-engines-agree guarantee: identical metrics."""
    monkeypatch.setattr("valcore.runner.build_agent", _constant_test_model_agent_builder("pass"))
    monkeypatch.setattr(
        "valcore.experiment.build_agent", _constant_test_model_agent_builder("pass")
    )

    run_result = _invoke(
        runner, db_path, "run", "evaluator", "judge", "--dataset", "cases", "--json"
    )
    assert run_result.exit_code == 0
    run_payload = json.loads(run_result.stdout)

    experiment_result = _invoke(
        runner, db_path, "run", "experiment", "judge", "--dataset", "cases", "--json"
    )
    assert experiment_result.exit_code == 0
    experiment_payload = json.loads(experiment_result.stdout)

    assert experiment_payload["status"] == "completed"
    assert experiment_payload["metrics"] == run_payload["metrics"]


def test_experiment_json_payload_shape_matches_run(runner, store, db_path, monkeypatch):
    monkeypatch.setattr("valcore.runner.build_agent", _constant_test_model_agent_builder("pass"))
    monkeypatch.setattr(
        "valcore.experiment.build_agent", _constant_test_model_agent_builder("pass")
    )

    run_payload = json.loads(
        _invoke(runner, db_path, "run", "evaluator", "judge", "--dataset", "cases", "--json").stdout
    )
    experiment_payload = json.loads(
        _invoke(
            runner, db_path, "run", "experiment", "judge", "--dataset", "cases", "--json"
        ).stdout
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


@pytest.fixture
def fresh_store() -> Iterator[Callable[[object], Store]]:
    """Open Stores on (possibly import-created) dbs, disposing each one at teardown.

    An undisposed engine keeps its SQLite connection alive until the garbage collector
    finalises it, which surfaces as a ResourceWarning charged to whichever unrelated
    test happens to be running at that moment.
    """
    engines: list[Engine] = []

    def open_store(db_path) -> Store:
        engine = create_engine(db_path)
        init_db(engine)
        engines.append(engine)
        return Store(engine)

    try:
        yield open_store
    finally:
        for engine in engines:
            engine.dispose()


def test_import_bundled_round_trips(runner, store, db_path, tmp_path, fresh_store):
    out = tmp_path / "pkg.json"
    exported = _invoke(
        runner, db_path, "export", "judge", "--dataset", "cases", "--format", "json", "-o", str(out)
    )
    assert exported.exit_code == 0

    dest = tmp_path / "imported.db"
    imported = _invoke(runner, dest, "import", str(out))
    assert imported.exit_code == 0

    s = fresh_store(dest)

    datasets = s.list_datasets()
    assert len(datasets) == 1
    ds = datasets[0]
    assert ds.name == "cases"
    rows = s.list_rows(ds.id)
    assert [r.data for r in rows] == [{"input": f"in{i}", "output": f"out{i}"} for i in range(4)]

    # Ground truth round-trips onto a LabelSet/Annotation pair, not DatasetRow.label.
    label_sets = s.list_label_sets(ds.id)
    assert len(label_sets) == 1
    annotations = s.list_annotations_for_rows(label_sets[0].id, [r.id for r in rows])
    by_row = {a.dataset_row_id: a for a in annotations}
    assert [by_row[r.id].labels for r in rows] == [["pass"], ["fail"], ["pass"], ["fail"]]

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


def test_import_name_override(runner, store, db_path, tmp_path, fresh_store):
    out = tmp_path / "pkg.json"
    _invoke(runner, db_path, "export", "--dataset", "cases", "--format", "json", "-o", str(out))

    dest = tmp_path / "imported.db"
    result = _invoke(runner, dest, "import", str(out), "--name", "renamed-cases")
    assert result.exit_code == 0

    s = fresh_store(dest)
    assert [d.name for d in s.list_datasets()] == ["renamed-cases"]


def test_import_invalid_agent_persists_nothing(runner, store, db_path, tmp_path, fresh_store):
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
    s = fresh_store(dest)
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
    assert cfg.logfire_read_key == "lf-key-9999"
    assert cfg.logfire_write_key == "lf-key-9999"
    assert cfg.logfire_token == "lf-existing-token"


def test_config_set_logfire_key_prompts_when_omitted(runner, db_path):
    result = _invoke(runner, db_path, "config", "set-logfire-key", input="lf-key-prompted\n")
    assert result.exit_code == 0
    assert load_config().logfire_read_key == "lf-key-prompted"
    assert load_config().logfire_write_key == "lf-key-prompted"
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
    assert after["logfire_read_key"] != before["logfire_read_key"]
    assert after["logfire_write_key"] != before["logfire_write_key"]
    assert after["logfire_token"] != "lf-secret-token"
    assert after["logfire_api_key"] != "lf-secret-apikey"
    assert after["logfire_read_key"] is True
    assert after["logfire_write_key"] is True
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


def test_config_set_logfire_read_and_write_keys_persist_independently(runner, db_path):
    result = _invoke(runner, db_path, "config", "set-logfire-read-key", "lf-read-only")
    assert result.exit_code == 0
    result = _invoke(runner, db_path, "config", "set-logfire-write-key", "lf-write-only")
    assert result.exit_code == 0
    cfg = load_config()
    assert cfg.logfire_read_key == "lf-read-only"
    assert cfg.logfire_write_key == "lf-write-only"


# -- logfire --------------------------------------------------------------------


def test_logfire_push_prints_id_name_and_case_count_never_a_url(
    runner, store, db_path, monkeypatch
):
    async def fake_push_dataset(
        dataset,
        rows,
        *,
        label_set=None,
        annotations=None,
        api_key=None,
        name=None,
        description=None,
        on_conflict="update",
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
        dataset,
        rows,
        *,
        label_set=None,
        annotations=None,
        api_key=None,
        name=None,
        description=None,
        on_conflict="update",
    ):
        captured["dataset_name"] = dataset.name
        captured["row_count"] = len(rows)
        return {"id": "x", "name": dataset.name, "case_count": len(rows), "output_schema": None}

    monkeypatch.setattr("valcore.logfire_io.push_dataset", fake_push_dataset)
    result = _invoke(runner, db_path, "logfire", "push", "cases")
    assert result.exit_code == 0
    assert captured == {"dataset_name": "cases", "row_count": 4}


def test_logfire_push_forwards_ground_truth_from_the_datasets_label_set(
    runner, store, db_path, monkeypatch
):
    """Finding 1 regression: the CLI's ``logfire push`` must forward ground truth.

    The ``cases`` fixture dataset carries a confirmed ``quality`` label set (see ``_seed``),
    so a correct push must resolve and pass it through rather than dropping it silently.
    """
    captured = {}

    async def fake_push_dataset(
        dataset,
        rows,
        *,
        label_set=None,
        annotations=None,
        api_key=None,
        name=None,
        description=None,
        on_conflict="update",
    ):
        captured["label_set"] = label_set
        captured["annotations"] = annotations
        return {"id": "x", "name": dataset.name, "case_count": len(rows), "output_schema": None}

    monkeypatch.setattr("valcore.logfire_io.push_dataset", fake_push_dataset)
    result = _invoke(runner, db_path, "logfire", "push", "cases")
    assert result.exit_code == 0
    assert captured["label_set"] is not None
    assert captured["label_set"].name == "quality"
    assert captured["annotations"] is not None
    confirmed = [a for a in captured["annotations"] if a.labels]
    assert len(confirmed) == 4


def test_logfire_push_defaults_have_no_name_or_description(runner, store, db_path, monkeypatch):
    calls = {}

    async def fake_push_dataset(
        dataset,
        rows,
        *,
        label_set=None,
        annotations=None,
        api_key=None,
        name=None,
        description=None,
        on_conflict="update",
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
        dataset,
        rows,
        *,
        label_set=None,
        annotations=None,
        api_key=None,
        name=None,
        description=None,
        on_conflict="update",
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


def test_logfire_push_no_api_key_exits_nonzero_naming_set_logfire_write_key(runner, store, db_path):
    # No stub installed: with no key configured, `push_dataset` must fail before any
    # network-facing import or call, exactly as `test_logfire_io.py` pins directly.
    result = _invoke(runner, db_path, "logfire", "push", "cases")
    assert result.exit_code != 0
    assert "valcore config set-logfire-write-key" in result.stderr


def test_config_set_logfire_explore_url_persists(runner, db_path):
    result = _invoke(
        runner,
        db_path,
        "config",
        "set-logfire-explore-url",
        "https://logfire-us.pydantic.dev/duncan/agent-tracing/explore",
    )
    assert result.exit_code == 0
    assert load_config().logfire_explore_url == (
        "https://logfire-us.pydantic.dev/duncan/agent-tracing/explore"
    )


def test_logfire_pull_creates_dataset_from_stubbed_query(runner, db_path, monkeypatch):
    from datetime import UTC, datetime

    from valcore.logfire_pull import PullResult

    async def fake_pull_records(sql: str, sample_n: int, **kwargs: object) -> PullResult:
        return PullResult(
            columns=["span_id", "message"],
            prepared=[{"data": {"span_id": "a", "message": "m"}}],
            row_annotations=[None],
            sql=sql,
            sample_n=sample_n,
            seed=kwargs.get("seed") or 3,
            min_timestamp=datetime(2026, 9, 2, tzinfo=UTC),
            max_timestamp=None,
            label_column=None,
        )

    monkeypatch.setattr("valcore.logfire_pull.pull_records", fake_pull_records)
    result = _invoke(
        runner,
        db_path,
        "logfire",
        "pull",
        "--sql",
        "SELECT span_id FROM records",
        "--name",
        "pulled",
        "--count",
        "4",
        "--seed",
        "3",
    )
    assert result.exit_code == 0, result.stderr
    assert "pulled" in result.output


def test_logfire_pull_requires_sql_or_sql_file(runner, db_path):
    result = _invoke(runner, db_path, "logfire", "pull", "--name", "x", "--count", "1")
    assert result.exit_code != 0


def test_logfire_list_prints_hosted_dataset_names(runner, db_path, monkeypatch):
    async def fake_list(*, api_key=None):
        return [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "name": "qa-set",
                "description": "Q&A",
                "case_count": 12,
            }
        ]

    monkeypatch.setattr("valcore.logfire_io.list_hosted_datasets", fake_list)
    result = _invoke(runner, db_path, "logfire", "list")
    assert result.exit_code == 0, result.stderr
    assert "qa-set" in result.output
    assert "12" in result.output


def test_logfire_fetch_creates_local_dataset(runner, db_path, monkeypatch, fresh_store):
    from valcore.logfire_io import HostedFetch

    async def fake_fetch(id_or_name, *, api_key=None):
        return HostedFetch(
            source_name=id_or_name,
            name="qa-set",
            columns=["question"],
            label_schema={"kind": "categorical", "labels": ["yes"]},
            prepared=[{"data": {"question": "Q1"}}],
            row_annotations=[{"labels": ["yes"], "source": LabelSource.MANUAL}],
        )

    monkeypatch.setattr("valcore.logfire_io.fetch_hosted_dataset", fake_fetch)
    result = _invoke(runner, db_path, "logfire", "fetch", "qa-set")
    assert result.exit_code == 0, result.stderr
    assert "qa-set" in result.output

    s = fresh_store(db_path)
    ds = s.get_dataset(s.list_datasets()[0].id)
    # The schema lives on a LabelSet.
    label_sets = s.list_label_sets(ds.id)
    assert len(label_sets) == 1
    rows = s.list_rows(ds.id)
    annotations = s.list_annotations_for_rows(label_sets[0].id, [r.id for r in rows])
    assert [a.labels for a in annotations] == [["yes"]]


def test_logfire_fetch_name_override(runner, db_path, store, monkeypatch):
    from valcore.logfire_io import HostedFetch

    async def fake_fetch(id_or_name, *, api_key=None):
        return HostedFetch(
            source_name=id_or_name,
            name="qa-set",
            columns=["question"],
            label_schema={},
            prepared=[{"data": {"question": "Q1"}}],
            row_annotations=[None],
        )

    monkeypatch.setattr("valcore.logfire_io.fetch_hosted_dataset", fake_fetch)
    result = _invoke(
        runner, db_path, "logfire", "fetch", "qa-set", "--name", "local-copy", "--description", "d"
    )
    assert result.exit_code == 0, result.stderr
    created = [ds for ds in store.list_datasets() if ds.name == "local-copy"]
    assert len(created) == 1
    assert created[0].description == "d"


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


# -- config set / unset -------------------------------------------------------


def test_config_set_model_persists_and_preserves_others(runner, db_path):
    _invoke(runner, db_path, "config", "set-key", "sk-secret-1234")
    result = _invoke(runner, db_path, "config", "set", "model", "gateway/openai:gpt-5")
    assert result.exit_code == 0

    cfg = load_config()
    assert cfg.model == "gateway/openai:gpt-5"
    assert cfg.gateway_api_key == "sk-secret-1234"


def test_config_set_model_rejects_a_malformed_string(runner, db_path):
    result = _invoke(runner, db_path, "config", "set", "model", "claude-sonnet-5")
    assert result.exit_code == 1
    assert load_config().model is None


def test_config_set_model_accepts_a_local_cli_route(runner, db_path):
    result = _invoke(runner, db_path, "config", "set", "model", "local/claude")
    assert result.exit_code == 0
    assert load_config().model == "local/claude"


def test_config_set_local_cli_default_persists(runner, db_path):
    result = _invoke(runner, db_path, "config", "set", "local_cli_default", "claude")
    assert result.exit_code == 0
    assert load_config().local_cli_default == "claude"


def test_config_set_local_cli_default_rejects_an_unknown_cli(runner, db_path):
    result = _invoke(runner, db_path, "config", "set", "local_cli_default", "aider")
    assert result.exit_code == 1
    assert "claude" in result.stderr
    assert load_config().local_cli_default is None


def test_config_set_concurrency_stores_an_int(runner, db_path):
    result = _invoke(runner, db_path, "config", "set", "concurrency", "16")
    assert result.exit_code == 0
    assert load_config().concurrency == 16


def test_config_set_concurrency_rejects_a_non_integer(runner, db_path):
    result = _invoke(runner, db_path, "config", "set", "concurrency", "lots")
    assert result.exit_code == 1
    assert load_config().concurrency is None


def test_config_set_db_path_stores_a_path(runner, db_path, tmp_path):
    target = tmp_path / "other.sqlite"
    result = _invoke(runner, db_path, "config", "set", "db_path", str(target))
    assert result.exit_code == 0
    assert load_config().db_path == target


def test_config_set_frontend_observability_fields(runner, db_path):
    trace_url = "https://logfire-us.pydantic.dev/v1/traces"
    assert (
        _invoke(runner, db_path, "config", "set", "logfire_frontend_trace_url", trace_url).exit_code
        == 0
    )
    token_result = _invoke(
        runner, db_path, "config", "set", "logfire_frontend_token", "lf-frontend-public"
    )
    replay_result = _invoke(runner, db_path, "config", "set", "logfire_session_replay", "true")

    assert token_result.exit_code == 0
    assert "lf-frontend-public" not in token_result.output
    assert replay_result.exit_code == 0
    cfg = load_config()
    assert cfg.logfire_frontend_trace_url == trace_url
    assert cfg.logfire_frontend_token == "lf-frontend-public"
    assert cfg.logfire_session_replay is True
    get_result = _invoke(runner, db_path, "config", "get", "--json", "--show-key")
    assert json.loads(get_result.output)["logfire_frontend_token"] is True
    assert "lf-frontend-public" not in get_result.output


def test_config_set_session_replay_rejects_a_non_boolean(runner, db_path):
    result = _invoke(runner, db_path, "config", "set", "logfire_session_replay", "sometimes")
    assert result.exit_code == 1
    assert load_config().logfire_session_replay is False


def test_config_set_rejects_an_unknown_key(runner, db_path):
    result = _invoke(runner, db_path, "config", "set", "nonsense", "x")
    assert result.exit_code == 1
    assert "nonsense" in result.stderr
    # The error names the keys that would have worked.
    assert "local_cli_default" in result.stderr


def test_config_set_does_not_echo_a_secret_value(runner, db_path):
    result = _invoke(runner, db_path, "config", "set", "gateway_api_key", "sk-secret-1234")
    assert result.exit_code == 0
    assert load_config().gateway_api_key == "sk-secret-1234"
    assert "sk-secret-1234" not in result.output


def test_config_set_rejects_the_legacy_combined_logfire_key(runner, db_path):
    result = _invoke(runner, db_path, "config", "set", "logfire_api_key", "lf-legacy")
    assert result.exit_code == 1
    assert "set-logfire-key" in result.stderr
    assert load_config().logfire_api_key is None


def test_config_unset_clears_one_key_and_preserves_others(runner, db_path):
    _invoke(runner, db_path, "config", "set-key", "sk-secret-1234")
    _invoke(runner, db_path, "config", "set", "local_cli_default", "codex")

    result = _invoke(runner, db_path, "config", "unset", "local_cli_default")
    assert result.exit_code == 0

    cfg = load_config()
    assert cfg.local_cli_default is None
    assert cfg.gateway_api_key == "sk-secret-1234"


def test_config_unset_clears_a_secret(runner, db_path):
    _invoke(runner, db_path, "config", "set-key", "sk-secret-1234")
    result = _invoke(runner, db_path, "config", "unset", "gateway_api_key")
    assert result.exit_code == 0
    assert load_config().gateway_api_key is None


def test_config_unset_clears_the_legacy_combined_logfire_key(runner, db_path):
    _invoke(runner, db_path, "config", "set-logfire-key", "lf-both")
    result = _invoke(runner, db_path, "config", "unset", "logfire_api_key")
    assert result.exit_code == 0
    assert load_config().logfire_api_key is None


def test_config_unset_rejects_an_unknown_key(runner, db_path):
    result = _invoke(runner, db_path, "config", "unset", "nonsense")
    assert result.exit_code == 1
    assert "nonsense" in result.stderr


def test_config_unset_an_already_unset_key_succeeds(runner, db_path):
    result = _invoke(runner, db_path, "config", "unset", "local_cli_default")
    assert result.exit_code == 0
    assert load_config().local_cli_default is None


def test_config_set_then_get_round_trips(runner, db_path):
    _invoke(runner, db_path, "config", "set", "local_cli_default", "cursor")
    result = _invoke(runner, db_path, "config", "get")
    assert result.exit_code == 0
    assert "cursor" in result.output


# -- agent prompt-sync -------------------------------------------------------
#
# The commands are a thin shell over ``AgentPromptSync``. Tests replace the service
# with a fake through the ``valcore.cli.main._prompt_sync_service(store)`` seam, so no
# Logfire call is ever made and the exact service calls can be asserted.

from valcore.agent_prompt_sync import SyncStatus, TemplateStatus
from valcore.errors import ConfigError, SyncConflictError

LOCAL_VERSION_ID = "local-version-0001"


def _template(
    key: str,
    state: str,
    *,
    local: str | None = "local text",
    base: str | None = "base text",
    remote: str | None = "remote text",
    remote_version: int | None = 4,
    base_remote_version: int | None = 3,
    error: str | None = None,
) -> TemplateStatus:
    return TemplateStatus(
        variable_name=f"valcore_agent_AGENT_{key}",
        state=state,  # type: ignore[arg-type]
        local_text=local,
        base_text=base,
        remote_text=remote,
        remote_version=remote_version,
        base_remote_version=base_remote_version,
        error=error,
    )


def _sync_status(
    instructions: str = "in_sync",
    input_template: str = "in_sync",
    *,
    linked: bool = True,
    revision: str = "rev",
    error: str | None = None,
    field_error: str | None = None,
    **texts,
) -> SyncStatus:
    return SyncStatus(
        linked=linked,
        local_version_id=LOCAL_VERSION_ID,
        revision=revision,
        error=error,
        templates={
            "instructions": _template("instructions", instructions, error=field_error, **texts),
            "input_template": _template(
                "input_template", input_template, error=field_error, **texts
            ),
        },
    )


class FakeSync:
    """Records service calls; every ``inspect`` hands out a fresh revision token."""

    def __init__(self, status: SyncStatus) -> None:
        self.status = status
        self.calls: list[tuple] = []
        self.inspects = 0
        self.last_revision = ""
        self.raises: Exception | None = None

    def inspect(self, agent_id: str) -> SyncStatus:
        self.inspects += 1
        self.last_revision = f"rev-{self.inspects}"
        self.calls.append(("inspect", agent_id))
        return self.status

    def _mutate(self, name: str, *args) -> SyncStatus:
        self.calls.append((name, *args))
        if self.raises is not None:
            raise self.raises
        return self.status

    def link(self, agent_id, initial, expected_revision):
        return self._mutate("link", agent_id, initial, expected_revision)

    def pull(self, agent_id, fields, expected_revision):
        return self._mutate("pull", agent_id, fields, expected_revision)

    def push(self, agent_id, fields, expected_revision):
        return self._mutate("push", agent_id, fields, expected_revision)

    def resolve(self, agent_id, fields, choice, expected_revision):
        return self._mutate("resolve", agent_id, list(fields), choice, expected_revision)

    def unlink(self, agent_id, expected_revision):
        return self._mutate("unlink", agent_id, expected_revision)

    def mutations(self) -> list[tuple]:
        return [call for call in self.calls if call[0] != "inspect"]


@pytest.fixture
def sync_agent(store, monkeypatch):
    """Seed one agent and install a fake sync service; return ``(agent, install)``."""
    agent = store.create_agent("writer", "Writes replies.")

    def install(status: SyncStatus) -> FakeSync:
        fake = FakeSync(status)
        monkeypatch.setattr("valcore.cli.main._prompt_sync_service", lambda _store: fake)
        return fake

    return agent, install


def _sync(runner, db_path, *args: str, **kwargs):
    return _invoke(runner, db_path, "agent", "prompt-sync", *args, **kwargs)


def test_prompt_sync_group_lists_every_subcommand(runner, db_path):
    result = _invoke(runner, db_path, "agent", "prompt-sync", "--help")
    assert result.exit_code == 0
    for name in ("status", "link", "pull", "push", "resolve", "unlink"):
        assert name in result.output


def test_prompt_sync_status_table_shows_variables_and_states(runner, db_path, sync_agent):
    _, install = sync_agent
    fake = install(
        _sync_status("local_changed", "remote_missing", remote=None, remote_version=None)
    )
    result = _sync(runner, db_path, "status", "writer")
    assert result.exit_code == 0, result.output
    assert "local_changed" in result.output
    assert "remote_missing" in result.output
    assert "valcore_agent_AGENT_instructions" in result.output
    assert fake.mutations() == []


def test_prompt_sync_status_table_shows_unsupported_field_error(runner, db_path, sync_agent):
    _, install = sync_agent
    install(_sync_status("unsupported", "in_sync", field_error="Save instructions as a string."))
    result = _sync(runner, db_path, "status", "writer")
    assert result.exit_code == 0, result.output
    assert "instructions" in result.output
    assert "Save instructions as a string." in result.output


def test_prompt_sync_status_resolves_agent_ref_to_agent_id(runner, db_path, sync_agent):
    agent, install = sync_agent
    fake = install(_sync_status())
    result = _sync(runner, db_path, "status", agent.id[:8])
    assert result.exit_code == 0, result.output
    assert fake.calls == [("inspect", agent.id)]


def test_prompt_sync_status_json_is_parseable_and_secret_free(runner, db_path, sync_agent):
    _, install = sync_agent
    install(_sync_status("remote_changed", "in_sync"))
    result = _sync(runner, db_path, "status", "writer", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["linked"] is True
    assert data["local_version_id"] == LOCAL_VERSION_ID
    assert data["templates"]["instructions"]["state"] == "remote_changed"
    assert data["templates"]["instructions"]["remote_text"] == "remote text"
    assert data["templates"]["input_template"]["state"] == "in_sync"
    assert "fingerprint" not in result.output.lower()


def test_prompt_sync_status_reports_status_error(runner, db_path, sync_agent):
    _, install = sync_agent
    install(_sync_status(error="Configure a Logfire key with project:write_variables."))
    result = _sync(runner, db_path, "status", "writer")
    assert result.exit_code != 0
    assert "project:write_variables" in result.output


def test_prompt_sync_unknown_agent_exits_nonzero_without_calling_service(
    runner, db_path, sync_agent
):
    _, install = sync_agent
    fake = install(_sync_status())
    result = _sync(runner, db_path, "status", "nope")
    assert result.exit_code == 1
    assert "nope" in result.output
    assert fake.calls == []


def test_prompt_sync_key_scope_error_is_reported_without_traceback(runner, db_path, sync_agent):
    _, install = sync_agent
    fake = install(_sync_status())

    def deny(agent_id):
        raise ConfigError(
            "The Logfire key needs project:read_variables and project:write_variables."
        )

    fake.inspect = deny  # type: ignore[method-assign]
    result = _sync(runner, db_path, "status", "writer")
    assert result.exit_code == 1
    assert "error:" in result.output
    assert "project:write_variables" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("initial", ["local", "remote"])
def test_prompt_sync_link_passes_initial_direction_and_fresh_revision(
    runner, db_path, sync_agent, initial
):
    agent, install = sync_agent
    fake = install(_sync_status(linked=False))
    result = _sync(runner, db_path, "link", "writer", "--initial", initial)
    assert result.exit_code == 0, result.output
    assert fake.mutations() == [("link", agent.id, initial, fake.last_revision)]
    assert fake.calls[0][0] == "inspect"


def test_prompt_sync_link_requires_a_valid_initial_direction(runner, db_path, sync_agent):
    _, install = sync_agent
    fake = install(_sync_status(linked=False))
    assert _sync(runner, db_path, "link", "writer").exit_code == 2
    assert _sync(runner, db_path, "link", "writer", "--initial", "both").exit_code == 2
    assert fake.calls == []


def test_prompt_sync_link_json_output(runner, db_path, sync_agent):
    _, install = sync_agent
    install(_sync_status())
    result = _sync(runner, db_path, "link", "writer", "--initial", "local", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["linked"] is True


@pytest.mark.parametrize("command", ["pull", "push"])
def test_prompt_sync_pull_push_previews_and_confirms_before_mutating(
    runner, db_path, sync_agent, command
):
    agent, install = sync_agent
    state = "remote_changed" if command == "pull" else "local_changed"
    fake = install(_sync_status(state, "in_sync", local="old local", remote="new remote"))
    result = _sync(runner, db_path, command, "writer", input="y\n")
    assert result.exit_code == 0, result.output
    assert "instructions" in result.output  # the proposal names the field
    assert fake.mutations() == [(command, agent.id, None, fake.last_revision)]
    assert fake.calls.index(("inspect", agent.id)) < fake.calls.index(fake.mutations()[0])


@pytest.mark.parametrize("command", ["pull", "push"])
def test_prompt_sync_pull_push_cancelled_confirmation_mutates_nothing(
    runner, db_path, sync_agent, command
):
    _, install = sync_agent
    state = "remote_changed" if command == "pull" else "local_changed"
    fake = install(_sync_status(state, "in_sync"))
    result = _sync(runner, db_path, command, "writer", input="n\n")
    assert result.exit_code != 0
    assert fake.mutations() == []


@pytest.mark.parametrize("command", ["pull", "push"])
def test_prompt_sync_pull_push_yes_skips_the_prompt(runner, db_path, sync_agent, command):
    agent, install = sync_agent
    state = "remote_changed" if command == "pull" else "local_changed"
    fake = install(_sync_status(state, "in_sync"))
    result = _sync(runner, db_path, command, "writer", "--yes")
    assert result.exit_code == 0, result.output
    assert fake.mutations() == [(command, agent.id, None, fake.last_revision)]


@pytest.mark.parametrize("command", ["pull", "push"])
def test_prompt_sync_pull_push_pass_selected_fields(runner, db_path, sync_agent, command):
    _agent, install = sync_agent
    state = "remote_changed" if command == "pull" else "local_changed"
    fake = install(_sync_status(state, state))
    result = _sync(runner, db_path, command, "writer", "--field", "input_template", "--yes")
    assert result.exit_code == 0, result.output
    (call,) = fake.mutations()
    assert call[0] == command
    assert list(call[2]) == ["input_template"]


def test_prompt_sync_pull_accepts_repeated_fields(runner, db_path, sync_agent):
    _, install = sync_agent
    fake = install(_sync_status("remote_changed", "remote_changed"))
    result = _sync(
        runner,
        db_path,
        "pull",
        "writer",
        "--field",
        "instructions",
        "--field",
        "input_template",
        "--yes",
    )
    assert result.exit_code == 0, result.output
    assert list(fake.mutations()[0][2]) == ["instructions", "input_template"]


def test_prompt_sync_rejects_unknown_field_before_any_call(runner, db_path, sync_agent):
    _, install = sync_agent
    fake = install(_sync_status("remote_changed", "in_sync"))
    result = _sync(runner, db_path, "pull", "writer", "--field", "model", "--yes")
    assert result.exit_code == 2
    assert fake.calls == []


@pytest.mark.parametrize("command", ["pull", "push"])
def test_prompt_sync_pull_push_with_nothing_eligible_does_not_mutate(
    runner, db_path, sync_agent, command
):
    _, install = sync_agent
    fake = install(_sync_status("in_sync", "in_sync"))
    result = _sync(runner, db_path, command, "writer", "--yes")
    assert result.exit_code == 0, result.output
    assert fake.mutations() == []


@pytest.mark.parametrize("command", ["pull", "push"])
def test_prompt_sync_conflict_exits_nonzero_and_prints_both_version_ids(
    runner, db_path, sync_agent, command
):
    _, install = sync_agent
    fake = install(_sync_status("conflict", "in_sync", remote_version=7, base_remote_version=5))
    result = _sync(runner, db_path, command, "writer", "--yes")
    assert result.exit_code != 0
    assert LOCAL_VERSION_ID in result.output
    assert "7" in result.output
    assert "resolve" in result.output
    assert fake.mutations() == []


@pytest.mark.parametrize("command", ["pull", "push"])
def test_prompt_sync_stale_revision_error_is_reported(runner, db_path, sync_agent, command):
    _, install = sync_agent
    state = "remote_changed" if command == "pull" else "local_changed"
    fake = install(_sync_status(state, "in_sync"))
    fake.raises = SyncConflictError("The active agent version changed; inspect sync again.")
    result = _sync(runner, db_path, command, "writer", "--yes")
    assert result.exit_code == 1
    assert "inspect sync again" in result.output
    assert "Traceback" not in result.output


def test_prompt_sync_pull_json_output(runner, db_path, sync_agent):
    _, install = sync_agent
    install(_sync_status("remote_changed", "in_sync"))
    result = _sync(runner, db_path, "pull", "writer", "--yes", "--json")
    assert result.exit_code == 0, result.output
    assert set(json.loads(result.stdout)["templates"]) == {"instructions", "input_template"}


@pytest.mark.parametrize("command", ["pull", "push"])
def test_prompt_sync_interactive_json_previews_on_stderr(runner, db_path, sync_agent, command):
    _, install = sync_agent
    state = "remote_changed" if command == "pull" else "local_changed"
    install(_sync_status(state, "in_sync", local="LOCAL-PROPOSAL", remote="REMOTE-PROPOSAL"))
    result = _sync(runner, db_path, command, "writer", "--json", input="y\n")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["linked"] is True
    assert "instructions" in result.stderr
    assert ("REMOTE-PROPOSAL" if command == "pull" else "LOCAL-PROPOSAL") in result.stderr


@pytest.mark.parametrize("command", ["pull", "push"])
def test_prompt_sync_default_skips_conflict_when_another_field_is_eligible(
    runner, db_path, sync_agent, command
):
    agent, install = sync_agent
    state = "remote_changed" if command == "pull" else "local_changed"
    fake = install(_sync_status("conflict", state))
    result = _sync(runner, db_path, command, "writer", "--yes")
    assert result.exit_code == 0, result.output
    assert fake.mutations() == [(command, agent.id, None, fake.last_revision)]


@pytest.mark.parametrize("command", ["pull", "push"])
def test_prompt_sync_explicit_conflict_fails_even_if_another_field_is_eligible(
    runner, db_path, sync_agent, command
):
    _, install = sync_agent
    state = "remote_changed" if command == "pull" else "local_changed"
    fake = install(_sync_status("conflict", state, remote_version=7))
    result = _sync(runner, db_path, command, "writer", "--field", "instructions", "--yes")
    assert result.exit_code == 1
    assert LOCAL_VERSION_ID in result.output
    assert "remote version 7" in result.output
    assert fake.mutations() == []


@pytest.mark.parametrize(
    ("command", "field", "message"),
    [
        ("push", "instructions", "Save instructions as a string."),
        ("pull", "input_template", "Remove Logfire composition blocks."),
    ],
)
def test_prompt_sync_unsupported_field_fails_with_reason(
    runner, db_path, sync_agent, command, field, message
):
    _, install = sync_agent
    states = {"instructions": "in_sync", "input_template": "in_sync"}
    states[field] = "unsupported"
    fake = install(
        _sync_status(states["instructions"], states["input_template"], field_error=message)
    )
    result = _sync(runner, db_path, command, "writer", "--yes")
    assert result.exit_code == 1
    assert field in result.output
    assert message in result.output
    assert fake.mutations() == []


def test_prompt_sync_resolve_requires_choice_and_field_even_with_yes(runner, db_path, sync_agent):
    _, install = sync_agent
    fake = install(_sync_status("conflict", "in_sync"))
    assert _sync(runner, db_path, "resolve", "writer", "--yes").exit_code == 2
    no_field = _sync(runner, db_path, "resolve", "writer", "--choice", "local", "--yes")
    assert no_field.exit_code == 2
    no_choice = _sync(runner, db_path, "resolve", "writer", "--field", "instructions", "--yes")
    assert no_choice.exit_code == 2
    bad = _sync(
        runner,
        db_path,
        "resolve",
        "writer",
        "--choice",
        "both",
        "--field",
        "instructions",
        "--yes",
    )
    assert bad.exit_code == 2
    assert fake.mutations() == []


@pytest.mark.parametrize("choice", ["local", "remote"])
def test_prompt_sync_resolve_shows_three_way_diff_then_calls_service(
    runner, db_path, sync_agent, choice
):
    agent, install = sync_agent
    fake = install(
        _sync_status(
            "conflict",
            "in_sync",
            local="LOCAL-SIDE",
            base="BASE-SIDE",
            remote="REMOTE-SIDE",
        )
    )
    result = _sync(
        runner,
        db_path,
        "resolve",
        "writer",
        "--choice",
        choice,
        "--field",
        "instructions",
        input="y\n",
    )
    assert result.exit_code == 0, result.output
    for text in ("LOCAL-SIDE", "BASE-SIDE", "REMOTE-SIDE"):
        assert text in result.output
    assert fake.mutations() == [("resolve", agent.id, ["instructions"], choice, fake.last_revision)]


def test_prompt_sync_resolve_shows_line_level_diffs(runner, db_path, sync_agent):
    _, install = sync_agent
    install(
        _sync_status(
            "conflict",
            "in_sync",
            base="shared\nold line\nunchanged",
            local="shared\nlocal line\nunchanged",
            remote="shared\nremote line\nunchanged",
        )
    )
    result = _sync(
        runner,
        db_path,
        "resolve",
        "writer",
        "--choice",
        "local",
        "--field",
        "instructions",
        "--yes",
    )
    assert result.exit_code == 0, result.output
    assert "--- base" in result.output
    assert "+++ local" in result.output
    assert "+++ remote" in result.output
    assert "-old line" in result.output
    assert "+local line" in result.output
    assert "+remote line" in result.output


def test_prompt_sync_resolve_interactive_json_shows_diff_on_stderr(runner, db_path, sync_agent):
    _, install = sync_agent
    install(_sync_status("conflict", "in_sync", base="old", local="local", remote="remote"))
    result = _sync(
        runner,
        db_path,
        "resolve",
        "writer",
        "--choice",
        "remote",
        "--field",
        "instructions",
        "--json",
        input="y\n",
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["linked"] is True
    assert "+++ local" in result.stderr
    assert "+++ remote" in result.stderr
    assert "+remote" in result.stderr


def test_prompt_sync_resolve_cancelled_confirmation_mutates_nothing(runner, db_path, sync_agent):
    _, install = sync_agent
    fake = install(_sync_status("conflict", "in_sync"))
    result = _sync(
        runner,
        db_path,
        "resolve",
        "writer",
        "--choice",
        "remote",
        "--field",
        "instructions",
        input="n\n",
    )
    assert result.exit_code != 0
    assert fake.mutations() == []


def test_prompt_sync_resolve_yes_skips_prompt_but_keeps_explicit_selection(
    runner, db_path, sync_agent
):
    agent, install = sync_agent
    fake = install(_sync_status("conflict", "conflict"))
    result = _sync(
        runner,
        db_path,
        "resolve",
        "writer",
        "--choice",
        "local",
        "--field",
        "input_template",
        "--yes",
    )
    assert result.exit_code == 0, result.output
    assert fake.mutations() == [
        ("resolve", agent.id, ["input_template"], "local", fake.last_revision)
    ]


def test_prompt_sync_resolve_stale_revision_error_is_reported(runner, db_path, sync_agent):
    _, install = sync_agent
    fake = install(_sync_status("conflict", "in_sync"))
    fake.raises = SyncConflictError("An agent template changed; inspect sync again.")
    result = _sync(
        runner,
        db_path,
        "resolve",
        "writer",
        "--choice",
        "local",
        "--field",
        "instructions",
        "--yes",
    )
    assert result.exit_code == 1
    assert "inspect sync again" in result.output


def test_prompt_sync_resolve_json_output(runner, db_path, sync_agent):
    _, install = sync_agent
    install(_sync_status("conflict", "in_sync"))
    result = _sync(
        runner,
        db_path,
        "resolve",
        "writer",
        "--choice",
        "local",
        "--field",
        "instructions",
        "--yes",
        "--json",
    )
    assert result.exit_code == 0, result.output
    assert "templates" in json.loads(result.stdout)


def test_prompt_sync_unlink_uses_fresh_revision(runner, db_path, sync_agent):
    agent, install = sync_agent
    fake = install(_sync_status())
    result = _sync(runner, db_path, "unlink", "writer", input="y\n")
    assert result.exit_code == 0, result.output
    assert fake.mutations() == [("unlink", agent.id, fake.last_revision)]
    assert fake.calls[0][0] == "inspect"
