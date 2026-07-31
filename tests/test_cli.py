"""Tests for the ``valcore`` CLI.

No network and no real home directory: ``VALCORE_HOME`` is pointed at ``tmp_path``
by the autouse fixture in ``conftest.py``, the store is a fresh ``tmp_path`` SQLite
DB, and agent behavior is driven by a ``FunctionModel`` injected via a monkeypatch
of ``valcore.runner.build_agent``.
"""

import json
from importlib.metadata import version as package_version

import pytest
from click.testing import CliRunner
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from valcore.cli.main import cli
from valcore.cli.resolve import resolve_dataset, resolve_evaluator, resolve_version
from valcore.errors import ContractError, NotFoundError
from valcore.factory import build_output_model
from valcore.models import LabelSource, ScoreKind
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


def _invoke(runner: CliRunner, db_path, *args: str, **kwargs):
    """Invoke the CLI with ``--db`` bound to the test database."""
    return runner.invoke(cli, ["--db", str(db_path), *args], **kwargs)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# -- version ------------------------------------------------------------------


def test_version(runner, db_path):
    result = _invoke(runner, db_path, "version")
    assert result.exit_code == 0
    assert result.output.strip() == package_version("valcore")


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
