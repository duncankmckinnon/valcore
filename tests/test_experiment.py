"""Tests for the experiment engine: execution over ``pydantic_evals.Dataset.evaluate``.

No network: agent behavior is driven by ``FunctionModel`` agents. ``execute_experiment``
exposes no ``agent=`` override (unlike ``runner.execute_run``), so tests monkeypatch
``valcore.experiment.build_agent`` to hand back a network-free test agent instead of one
built from the version's live model string.

The most important test here is ``test_experiment_and_run_agree``: both engines must
report identical metrics and per-row agreement over the same version and dataset, since
that is the whole point of routing both through ``metrics.compute_metrics``.
"""

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from valcore.errors import ContractError
from valcore.factory import build_output_model
from valcore.models import LabelSource, RunKind, RunStatus, ScoreKind
from valcore.runner import RunEvent, execute_run
from valcore.store import Store, create_engine, init_db

CATEGORICAL_SCHEMA = {"kind": "categorical", "labels": ["pass", "fail"]}
NUMERIC_SCHEMA = {"kind": "numeric"}

CATEGORICAL_VERSION_FIELDS = {
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

NUMERIC_VERSION_FIELDS = {
    "version_name": "v-numeric",
    "model": "gateway/anthropic:claude-sonnet-5",
    "instructions": "Score the row.",
    "prompt_template": "Input: {input} Output: {output}",
    "required_columns": ["input", "output"],
    "output_fields": [
        {"name": "score", "type": "float", "description": "a numeric score"},
    ],
    "score_field": "score",
    "score_kind": ScoreKind.NUMERIC,
}


@pytest.fixture
def store(tmp_path) -> Store:
    """A real Store backed by a fresh SQLite DB under tmp_path."""
    engine = create_engine(tmp_path / "eval.db")
    init_db(engine)
    return Store(engine)


def make_version(store: Store, **overrides):
    """Create an evaluator and a valid categorical version, returning the version."""
    evaluator = store.create_evaluator("ev")
    fields = {**CATEGORICAL_VERSION_FIELDS, **overrides}
    return store.create_version(evaluator.id, **fields)


def make_numeric_version(store: Store, **overrides):
    """Create an evaluator and a valid numeric version, returning the version."""
    evaluator = store.create_evaluator("ev-numeric")
    fields = {**NUMERIC_VERSION_FIELDS, **overrides}
    return store.create_version(evaluator.id, **fields)


def make_dataset(
    store: Store,
    labels: list[str | float | None],
    *,
    columns: list[str] | None = None,
    schema: dict | None = None,
):
    """Create a dataset with one row per entry in ``labels`` (None = unlabeled)."""
    dataset = store.create_dataset(
        "ds",
        "",
        columns if columns is not None else ["input", "output"],
        schema if schema is not None else CATEGORICAL_SCHEMA,
    )
    rows = store.add_rows(
        dataset.id, [{"input": f"in{i}", "output": f"out{i}"} for i in range(len(labels))]
    )
    for row, label in zip(rows, labels, strict=True):
        if label is not None:
            store.set_label(row.id, {"value": label}, LabelSource.MANUAL)
    return dataset


def constant_agent(version, verdict: str = "pass") -> Agent:
    """An agent whose model always emits the given categorical verdict."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"verdict": verdict})])

    return Agent(FunctionModel(respond), output_type=build_output_model(version))


def constant_numeric_agent(version, score: float) -> Agent:
    """An agent whose model always emits the given numeric score."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"score": score})])

    return Agent(FunctionModel(respond), output_type=build_output_model(version))


def patch_build_agent(monkeypatch: pytest.MonkeyPatch, agent: Agent) -> None:
    """Force ``execute_experiment`` to use a network-free test agent.

    ``execute_experiment`` has no ``agent=`` override (unlike ``runner.execute_run``), so
    the only way to avoid a real model call in a test is to intercept the factory call
    it makes internally.
    """
    monkeypatch.setattr("valcore.experiment.build_agent", lambda version: agent)


# -- Experiment and run agree ---------------------------------------------------


@pytest.mark.anyio
async def test_experiment_and_run_agree(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    from valcore.experiment import execute_experiment

    version = make_version(store)
    dataset = make_dataset(store, ["pass", "fail", "pass", "fail", "pass"])

    run_via_runner = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=2)
    run_via_experiment = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=2)

    runner_result = await execute_run(store, run_via_runner.id, agent=constant_agent(version))

    patch_build_agent(monkeypatch, constant_agent(version))
    experiment_result = await execute_experiment(store, run_via_experiment.id)

    assert runner_result.status is RunStatus.COMPLETED
    assert experiment_result.status is RunStatus.COMPLETED
    assert runner_result.metrics == experiment_result.metrics

    runner_scores = {r.row_id: r.score_value for r in store.list_results(run_via_runner.id)}
    experiment_scores = {r.row_id: r.score_value for r in store.list_results(run_via_experiment.id)}
    assert runner_scores == experiment_scores

    runner_agreement = {r.row_id: r.agreement for r in store.list_results(run_via_runner.id)}
    experiment_agreement = {
        r.row_id: r.agreement for r in store.list_results(run_via_experiment.id)
    }
    assert runner_agreement == experiment_agreement


@pytest.mark.anyio
async def test_experiment_and_run_agree_numeric(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same equivalence holds for numeric scores, exercising NumericDelta end to end."""
    from valcore.experiment import execute_experiment

    version = make_numeric_version(store)
    dataset = make_dataset(
        store, [1.0, 4.0, 2.5, 9.0, 0.0], columns=["input", "output"], schema=NUMERIC_SCHEMA
    )

    run_via_runner = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=2)
    run_via_experiment = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=2)

    runner_result = await execute_run(
        store, run_via_runner.id, agent=constant_numeric_agent(version, 5.0)
    )

    patch_build_agent(monkeypatch, constant_numeric_agent(version, 5.0))
    experiment_result = await execute_experiment(store, run_via_experiment.id)

    assert runner_result.metrics == experiment_result.metrics

    runner_agreement = {r.row_id: r.agreement for r in store.list_results(run_via_runner.id)}
    experiment_agreement = {
        r.row_id: r.agreement for r in store.list_results(run_via_experiment.id)
    }
    assert runner_agreement == experiment_agreement
    # Signed deltas, not booleans; at least one row has a non-zero delta.
    assert any(v != 0 for v in experiment_agreement.values())


# -- NumericDelta matches runner._agreement --------------------------------------


@pytest.mark.parametrize(
    "predicted,label",
    [
        (5.0, 5.0),
        (5.0, 3.0),
        (3.0, 5.0),
        (-2.0, 4.0),
        (0.0, 0.0),
        (10.5, 10.5),
        (-1.5, -3.5),
    ],
)
def test_numeric_delta_matches_agreement(predicted: float, label: float) -> None:
    """``NumericDelta.evaluate`` is exactly the signed delta ``runner._agreement`` computes."""
    from types import SimpleNamespace

    from valcore.experiment import NumericDelta

    from valcore.runner import _agreement

    ctx = SimpleNamespace(output=predicted, expected_output=label)
    evaluator = NumericDelta()

    expected = _agreement(ScoreKind.NUMERIC, predicted, label)
    assert evaluator.evaluate(ctx) == pytest.approx(expected)
    # In particular, negative deltas survive (not, e.g., accidentally abs()'d).
    if predicted < label:
        assert evaluator.evaluate(ctx) < 0


# -- Categorical agreement mirrors EqualsExpected --------------------------------


@pytest.mark.anyio
async def test_categorical_agreement_is_exact_match(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from valcore.experiment import execute_experiment

    version = make_version(store)
    dataset = make_dataset(store, ["pass", "fail", "pass", "fail", "pass"])
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=2)

    patch_build_agent(monkeypatch, constant_agent(version, verdict="pass"))
    result = await execute_experiment(store, run.id)

    assert result.status is RunStatus.COMPLETED
    rows = store.list_rows(dataset.id)
    results = {r.row_id: r for r in store.list_results(run.id)}
    expected_agreement = [True, False, True, False, True]
    for row, expected in zip(rows, expected_agreement, strict=True):
        assert results[row.id].agreement is expected
        assert isinstance(results[row.id].agreement, bool)
        assert results[row.id].score_value == "pass"
    assert result.metrics is not None
    assert result.metrics["n"] == 5
    assert result.metrics["accuracy"] == pytest.approx(3 / 5)


# -- EVAL runs attach no agreement evaluator -------------------------------------


@pytest.mark.anyio
async def test_eval_run_has_no_agreement(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    """An EVAL-kind run computes no agreement even when the dataset carries labels."""
    from valcore.experiment import execute_experiment

    version = make_version(store)
    dataset = make_dataset(store, ["pass", "fail", "pass"])
    run = store.create_run(RunKind.EVAL, version.id, dataset.id, concurrency=2)

    patch_build_agent(monkeypatch, constant_agent(version))
    result = await execute_experiment(store, run.id)

    assert result.status is RunStatus.COMPLETED
    assert result.metrics is None
    results = store.list_results(run.id)
    assert len(results) == 3
    assert all(r.agreement is None for r in results)
    assert all(r.error is None for r in results)


# -- One result and one event per case -------------------------------------------


@pytest.mark.anyio
async def test_one_result_and_one_event_per_case(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from valcore.experiment import execute_experiment

    version = make_version(store)
    dataset = make_dataset(store, ["pass", "fail", "pass"])
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=2)

    events: list[RunEvent] = []

    async def on_event(event: RunEvent) -> None:
        events.append(event)

    patch_build_agent(monkeypatch, constant_agent(version))
    await execute_experiment(store, run.id, on_event=on_event)

    assert len(store.list_results(run.id)) == 3
    kinds = [e.type for e in events]
    assert kinds.count("started") == 1
    assert kinds.count("finished") == 1
    assert kinds.count("row") == 3


@pytest.mark.anyio
async def test_case_failure_is_recorded_not_raised(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing case's ``teardown`` records an error rather than raising or being dropped."""
    from valcore.experiment import execute_experiment

    version = make_version(store)
    dataset = make_dataset(store, ["pass", "pass", "pass", "pass", "pass"])
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=1)

    calls = [0]

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls[0] += 1
        if calls[0] == 3:
            raise RuntimeError("boom on third row")
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"verdict": "pass"})])

    agent = Agent(FunctionModel(respond), output_type=build_output_model(version))

    events: list[RunEvent] = []

    async def on_event(event: RunEvent) -> None:
        events.append(event)

    patch_build_agent(monkeypatch, agent)
    result = await execute_experiment(store, run.id, on_event=on_event)

    assert result.status is RunStatus.COMPLETED_WITH_ERRORS
    results = store.list_results(run.id)
    assert len(results) == 5
    errored = [r for r in results if r.error is not None]
    assert len(errored) == 1
    assert "boom on third row" in errored[0].error
    assert errored[0].output is None
    assert [e.type for e in events].count("row") == 5
    # Metrics computed over the 4 successes only, matching the runner.
    assert result.metrics is not None
    assert result.metrics["n"] == 4


# -- ExperimentRun marker and cancellation ---------------------------------------


@pytest.mark.anyio
async def test_experiment_run_marker_written_and_blocks_cancel(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from valcore.experiment import execute_experiment

    version = make_version(store)
    dataset = make_dataset(store, ["pass", "fail", "pass", "fail"])
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=2)

    assert store.get_experiment(run.id) is None

    patch_build_agent(monkeypatch, constant_agent(version))
    result = await execute_experiment(store, run.id)

    assert result.status is RunStatus.COMPLETED
    experiment = store.get_experiment(run.id)
    assert experiment is not None
    assert experiment.experiment_name == version.version_name
    assert experiment.case_count == 4

    with pytest.raises(ContractError):
        store.request_cancel(run.id)


@pytest.mark.anyio
async def test_experiment_run_case_count_excludes_failed_cases(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``case_count`` reflects ``report.cases`` (successes), not the total row count."""
    from valcore.experiment import execute_experiment

    version = make_version(store)
    dataset = make_dataset(store, ["pass", "pass", "pass", "pass", "pass"])
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=1)

    calls = [0]

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls[0] += 1
        if calls[0] == 2:
            raise RuntimeError("boom")
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"verdict": "pass"})])

    agent = Agent(FunctionModel(respond), output_type=build_output_model(version))

    patch_build_agent(monkeypatch, agent)
    result = await execute_experiment(store, run.id)

    assert result.status is RunStatus.COMPLETED_WITH_ERRORS
    experiment = store.get_experiment(run.id)
    assert experiment is not None
    assert experiment.case_count == 4


# -- Incompatible dataset ---------------------------------------------------------


@pytest.mark.anyio
async def test_incompatible_dataset_fails(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    from valcore.experiment import execute_experiment

    version = make_version(store)
    # Dataset missing the required "output" column.
    dataset = make_dataset(store, ["pass", "fail"], columns=["input"])
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=2)

    patch_build_agent(monkeypatch, constant_agent(version))
    result = await execute_experiment(store, run.id)

    assert result.status is RunStatus.FAILED
    assert result.error is not None
    assert "output" in result.error
    assert store.list_results(run.id) == []
    assert store.get_experiment(run.id) is None
