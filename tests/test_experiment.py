"""Tests for the experiment engine: execution over ``pydantic_evals.Dataset.evaluate``.

No network: agent behavior is driven by ``TestModel`` agents throughout. A single
``_FlakyTestModel`` subclass (still ``TestModel``-based -- it delegates to
``TestModel.request`` for every call except the one under test) supplies the minimum
custom behavior needed to make one specific call fail, for the tests that exercise a
row/case failure. ``execute_experiment`` exposes no ``agent=`` override (unlike
``runner.execute_run``), so tests monkeypatch ``valcore.experiment.build_agent`` to hand
back a network-free test agent instead of one built from the version's live model string.

The most important test here is ``test_experiment_and_run_agree``: both engines must
report identical metrics and per-row agreement over the same version and dataset, since
that is the whole point of routing both through ``metrics.compute_metrics``.
"""

import asyncio
import importlib.util

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from valcore import tracing
from valcore.errors import ContractError
from valcore.factory import build_output_model
from valcore.models import LabelSource, RunKind, RunStatus, ScoreKind
from valcore.runner import RunEvent, execute_run
from valcore.store import Store, create_engine, init_db

_LOGFIRE_PRESENT = importlib.util.find_spec("logfire") is not None

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


class _FlakyTestModel(TestModel):
    """A ``TestModel`` that raises on one specific call, to exercise a case failure.

    Every call except ``fail_on_call`` behaves exactly like plain ``TestModel`` (delegating
    to ``custom_output_args``); this is the minimum custom behavior needed to inject a
    single failing row without reaching for a fully custom model.
    """

    def __init__(self, *, fail_on_call: int, error_message: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._fail_on_call = fail_on_call
        self._error_message = error_message
        self._calls = 0

    async def request(self, messages, model_settings, model_request_parameters):
        """Raise on the ``fail_on_call``-th invocation; otherwise defer to ``TestModel``."""
        self._calls += 1
        if self._calls == self._fail_on_call:
            raise RuntimeError(self._error_message)
        return await super().request(messages, model_settings, model_request_parameters)


def constant_agent(version, verdict: str = "pass") -> Agent:
    """An agent whose model always emits the given categorical verdict."""
    model = TestModel(custom_output_args={"verdict": verdict})
    return Agent(model, output_type=build_output_model(version))


def constant_numeric_agent(version, score: float) -> Agent:
    """An agent whose model always emits the given numeric score."""
    model = TestModel(custom_output_args={"score": score})
    return Agent(model, output_type=build_output_model(version))


def flaky_agent(version, *, fail_on_call: int, verdict: str = "pass", error_message: str) -> Agent:
    """An agent whose ``fail_on_call``-th row raises; every other row emits ``verdict``."""
    model = _FlakyTestModel(
        fail_on_call=fail_on_call,
        error_message=error_message,
        custom_output_args={"verdict": verdict},
    )
    return Agent(model, output_type=build_output_model(version))


def patch_build_agent(monkeypatch: pytest.MonkeyPatch, agent: Agent) -> None:
    """Force ``execute_experiment`` to use a network-free test agent.

    ``execute_experiment`` has no ``agent=`` override (unlike ``runner.execute_run``), so
    the only way to avoid a real model call in a test is to intercept the factory call
    it makes internally.
    """
    monkeypatch.setattr("valcore.experiment.build_agent", lambda version: agent)


def capture_evaluators(monkeypatch: pytest.MonkeyPatch) -> list:
    """Patch ``dataset_to_evals`` to record the evaluators list it is actually called with.

    Agreement and metrics are recomputed independently from persisted ``RunResult`` rows,
    so a test that only inspects the final results would still pass if the wrong evaluator
    (or none at all) were attached to the constructed dataset. Capturing the real call is
    the only way to verify the task/evaluator mapping itself.
    """
    import valcore.experiment as experiment_module

    captured: list = []
    original = experiment_module.dataset_to_evals

    def spy(dataset, rows, evaluators):
        captured.append(evaluators)
        return original(dataset, rows, evaluators)

    monkeypatch.setattr(experiment_module, "dataset_to_evals", spy)
    return captured


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
    from pydantic_evals.evaluators.common import EqualsExpected

    from valcore.experiment import execute_experiment

    version = make_version(store)
    dataset = make_dataset(store, ["pass", "fail", "pass", "fail", "pass"])
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=2)

    evaluator_calls = capture_evaluators(monkeypatch)
    patch_build_agent(monkeypatch, constant_agent(version, verdict="pass"))
    result = await execute_experiment(store, run.id)

    # The construction itself carries EqualsExpected, not just the agreement values that
    # get recomputed independently during persistence.
    assert len(evaluator_calls) == 1
    (evaluators,) = evaluator_calls
    assert len(evaluators) == 1
    assert isinstance(evaluators[0], EqualsExpected)

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


@pytest.mark.anyio
async def test_numeric_run_attaches_numeric_delta(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A numeric VALIDATION run's constructed dataset carries ``NumericDelta``, not ``EqualsExpected``.

    The two evaluators produce identical agreement only by coincidence on exact matches,
    so this must be checked at construction time, not inferred from persisted results.
    """
    from valcore.experiment import NumericDelta, execute_experiment

    version = make_numeric_version(store)
    dataset = make_dataset(store, [1.0, 2.0], columns=["input", "output"], schema=NUMERIC_SCHEMA)
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=2)

    evaluator_calls = capture_evaluators(monkeypatch)
    patch_build_agent(monkeypatch, constant_numeric_agent(version, 1.0))
    await execute_experiment(store, run.id)

    assert len(evaluator_calls) == 1
    (evaluators,) = evaluator_calls
    assert len(evaluators) == 1
    assert isinstance(evaluators[0], NumericDelta)


# -- EVAL runs attach no agreement evaluator -------------------------------------


@pytest.mark.anyio
async def test_eval_run_has_no_agreement(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    """An EVAL-kind run computes no agreement even when the dataset carries labels."""
    from valcore.experiment import execute_experiment

    version = make_version(store)
    dataset = make_dataset(store, ["pass", "fail", "pass"])
    run = store.create_run(RunKind.EVAL, version.id, dataset.id, concurrency=2)

    evaluator_calls = capture_evaluators(monkeypatch)
    patch_build_agent(monkeypatch, constant_agent(version))
    result = await execute_experiment(store, run.id)

    # No agreement evaluator is attached at all -- not just "attached but ignored".
    assert evaluator_calls == [[]]

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

    agent = flaky_agent(version, fail_on_call=3, error_message="boom on third row")

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


@pytest.mark.anyio
async def test_teardown_none_records_error(store: Store) -> None:
    """``teardown(None)`` -- the interrupted-case path -- records an error, not a raise.

    ``Dataset.evaluate`` calls ``teardown(None)`` only when the run is interrupted before a
    report object exists for the case; there is no public way to trigger that through
    ``execute_experiment`` (this engine has no cancellation), so ``PersistResults`` is driven
    directly to exercise the branch the task/test plan calls out explicitly.
    """
    from pydantic_evals import Case

    from valcore.experiment import PersistResults

    version = make_version(store)
    dataset = make_dataset(store, ["pass"])
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=1)
    row = store.list_rows(dataset.id)[0]

    events: list[tuple[str, bool, str | float | None]] = []

    async def emit_row(row_id: str, success: bool, score_value: str | float | None) -> None:
        events.append((row_id, success, score_value))

    case = Case(name=row.id, inputs=row.data, expected_output="pass")
    lifecycle = PersistResults(
        case,
        store=store,
        run_id=run.id,
        version=version,
        want_agreement=True,
        rows_by_id={row.id: row},
        emit_row=emit_row,
    )

    await lifecycle.setup()
    await lifecycle.teardown(None)

    results = store.list_results(run.id)
    assert len(results) == 1
    assert results[0].error is not None
    assert results[0].output is None
    assert results[0].score_value is None
    assert events == [(row.id, False, None)]


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
async def test_request_cancel_raises_while_experiment_is_still_running(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``ExperimentRun`` marker must exist before the run reaches ``RUNNING``.

    A marker written only after ``evaluate()`` completes would leave ``request_cancel``
    finding no row for the entire active run, silently accepting a cancellation that
    ``evaluate()`` never polls -- exactly the race this finding closes. This blocks an
    in-flight model call so the run is still ``RUNNING`` when ``request_cancel`` is called.
    """
    from valcore.experiment import execute_experiment

    version = make_version(store)
    dataset = make_dataset(store, ["pass", "pass"])
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=1)

    release = asyncio.Event()

    class _BlockingTestModel(TestModel):
        """A ``TestModel`` whose call blocks until released, keeping the run RUNNING."""

        async def request(self, messages, model_settings, model_request_parameters):
            await release.wait()
            return await super().request(messages, model_settings, model_request_parameters)

    agent = Agent(
        _BlockingTestModel(custom_output_args={"verdict": "pass"}),
        output_type=build_output_model(version),
    )
    patch_build_agent(monkeypatch, agent)

    task = asyncio.create_task(execute_experiment(store, run.id))
    try:
        async with asyncio.timeout(5.0):
            while store.get_run(run.id).status is not RunStatus.RUNNING:
                await asyncio.sleep(0)

        with pytest.raises(ContractError):
            store.request_cancel(run.id)
    finally:
        release.set()
        result = await task

    assert result.status is RunStatus.COMPLETED
    assert store.get_run(run.id).cancel_requested is False


@pytest.mark.anyio
async def test_experiment_run_case_count_excludes_failed_cases(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``case_count`` reflects ``report.cases`` (successes), not the total row count."""
    from valcore.experiment import execute_experiment

    version = make_version(store)
    dataset = make_dataset(store, ["pass", "pass", "pass", "pass", "pass"])
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=1)

    agent = flaky_agent(version, fail_on_call=2, error_message="boom")

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


# -- Missing labels -----------------------------------------------------------------


@pytest.mark.anyio
async def test_partially_unlabeled_validation_dataset_raises(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A VALIDATION run must reject a dataset with even one unlabeled row.

    ``runner.execute_run`` raises ``ContractError`` before entering ``RUNNING`` for this
    exact case; without the matching check here, this engine would silently evaluate the
    unlabeled row, persist ``agreement=None`` for it, and quietly compute metrics over
    only the labeled subset instead of refusing to run at all.
    """
    from valcore.experiment import execute_experiment

    version = make_version(store)
    dataset = make_dataset(store, ["pass", None, "fail"])
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=2)

    patch_build_agent(monkeypatch, constant_agent(version))
    with pytest.raises(ContractError):
        await execute_experiment(store, run.id)

    assert store.list_results(run.id) == []
    assert store.get_run(run.id).status is RunStatus.PENDING
    assert store.get_experiment(run.id) is None


@pytest.mark.anyio
async def test_wholly_unlabeled_validation_dataset_raises(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same rejection holds when no row at all carries a label."""
    from valcore.experiment import execute_experiment

    version = make_version(store)
    dataset = make_dataset(store, [None, None, None])
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=2)

    patch_build_agent(monkeypatch, constant_agent(version))
    with pytest.raises(ContractError):
        await execute_experiment(store, run.id)

    assert store.list_results(run.id) == []
    assert store.get_run(run.id).status is RunStatus.PENDING


@pytest.mark.anyio
async def test_eval_run_tolerates_unlabeled_rows(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The missing-label check is VALIDATION-only -- an EVAL run has no labels by design."""
    from valcore.experiment import execute_experiment

    version = make_version(store)
    dataset = make_dataset(store, [None, None, None])
    run = store.create_run(RunKind.EVAL, version.id, dataset.id, concurrency=2)

    patch_build_agent(monkeypatch, constant_agent(version))
    result = await execute_experiment(store, run.id)

    assert result.status is RunStatus.COMPLETED


# -- Unexpected lifecycle failures leave the run FAILED, not stuck RUNNING ---------


@pytest.mark.anyio
async def test_malformed_numeric_label_fails_run_not_stuck_running(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A label that can't be coerced to a float breaks ``_agreement`` inside ``teardown``.

    ``pydantic_evals`` explicitly propagates a lifecycle's exceptions to ``evaluate()``'s
    caller, so this must resolve to a terminal ``FAILED`` status with ``finished_at`` set
    and an ``error`` event -- not leave the run permanently ``RUNNING``.
    """
    from valcore.experiment import execute_experiment

    version = make_numeric_version(store)
    dataset = store.create_dataset("ds", "", ["input", "output"], NUMERIC_SCHEMA)
    rows = store.add_rows(dataset.id, [{"input": "in0", "output": "out0"}])
    store.set_label(rows[0].id, {"value": "not-a-number"}, LabelSource.MANUAL)
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=1)

    events: list[RunEvent] = []

    async def on_event(event: RunEvent) -> None:
        events.append(event)

    patch_build_agent(monkeypatch, constant_numeric_agent(version, 1.0))
    result = await execute_experiment(store, run.id, on_event=on_event)

    assert result.status is RunStatus.FAILED
    assert result.finished_at is not None
    assert result.error is not None
    assert [e.type for e in events].count("error") == 1


@pytest.mark.anyio
async def test_event_callback_failure_fails_run_not_stuck_running(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ``on_event`` callback that raises during a ``row`` event must not hang the run.

    ``teardown`` awaits the event callback directly, and the same "lifecycle exceptions
    propagate" rule applies here -- the run must resolve to FAILED with ``finished_at``
    set rather than being abandoned mid-``RUNNING``.
    """
    from valcore.experiment import execute_experiment

    version = make_version(store)
    dataset = make_dataset(store, ["pass", "fail"])
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=1)

    async def on_event(event: RunEvent) -> None:
        if event.type == "row":
            raise RuntimeError("callback exploded")

    patch_build_agent(monkeypatch, constant_agent(version))
    result = await execute_experiment(store, run.id, on_event=on_event)

    assert result.status is RunStatus.FAILED
    assert result.finished_at is not None
    assert result.error is not None


# -- Tracing ----------------------------------------------------------------------
#
# execute_experiment wraps the transition to RUNNING through the terminal status
# update in tracing.run_span, exactly as runner.execute_run does. These tests prove
# the span closes with a status attribute both on the happy path and when execution
# fails after entering the span (eval-dataset construction, evaluate() itself).


@pytest.fixture(autouse=True)
def _reset_tracing_configured_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset tracing's module-global idempotency guard before every test in this file."""
    monkeypatch.setattr(tracing, "_configured", False, raising=False)


@pytest.fixture
def traced(capfire, monkeypatch: pytest.MonkeyPatch):
    """Mark tracing as configured against ``capfire``'s in-memory exporter."""
    monkeypatch.setattr(tracing, "_configured", True)
    return capfire


@pytest.mark.skipif(not _LOGFIRE_PRESENT, reason="logfire extra not installed")
class TestExperimentSpan:
    """With tracing configured, execute_experiment must produce a real span tree."""

    @pytest.mark.anyio
    async def test_successful_run_span_carries_status_and_metrics(
        self, store: Store, traced, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from valcore.experiment import execute_experiment

        version = make_version(store)
        dataset = make_dataset(store, ["pass", "fail", "pass"])
        run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=2)

        patch_build_agent(monkeypatch, constant_agent(version))
        result = await execute_experiment(store, run.id)

        assert result.metrics is not None
        spans = traced.exporter.exported_spans_as_dict(parse_json_attributes=True)
        run_spans = [s for s in spans if s["name"] == "valcore.run"]
        assert len(run_spans) == 1
        assert run_spans[0]["end_time"] is not None
        attrs = run_spans[0]["attributes"]
        assert attrs["status"] == result.status.value
        for key, value in result.metrics.items():
            assert attrs[key] == value

    @pytest.mark.anyio
    async def test_eval_dataset_construction_failure_closes_span_as_failed(
        self, store: Store, traced, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failure building the ``EvalsDataset`` happens inside the span (it needs
        the agent and rows resolved during setup) and must still close the span with
        ``status=failed`` rather than leaving it open or attribute-less."""
        import valcore.experiment as experiment_module

        version = make_version(store)
        dataset = make_dataset(store, ["pass", "fail"])
        run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=1)

        def boom(dataset, rows, evaluators):
            raise RuntimeError("dataset construction exploded")

        monkeypatch.setattr(experiment_module, "dataset_to_evals", boom)
        patch_build_agent(monkeypatch, constant_agent(version))

        result = await experiment_module.execute_experiment(store, run.id)

        assert result.status is RunStatus.FAILED
        assert result.finished_at is not None
        spans = traced.exporter.exported_spans_as_dict()
        run_spans = [s for s in spans if s["name"] == "valcore.run"]
        assert len(run_spans) == 1
        assert run_spans[0]["end_time"] is not None
        assert run_spans[0]["attributes"]["status"] == RunStatus.FAILED.value

    @pytest.mark.anyio
    async def test_evaluate_failure_closes_span_as_failed(
        self, store: Store, traced, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A malformed label breaks ``_agreement`` inside ``teardown``, which
        ``pydantic_evals`` propagates out of ``evaluate()`` itself -- the span must
        still close with ``status=failed`` rather than staying open."""
        from valcore.experiment import execute_experiment

        version = make_numeric_version(store)
        dataset = store.create_dataset("ds", "", ["input", "output"], NUMERIC_SCHEMA)
        rows = store.add_rows(dataset.id, [{"input": "in0", "output": "out0"}])
        store.set_label(rows[0].id, {"value": "not-a-number"}, LabelSource.MANUAL)
        run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=1)

        patch_build_agent(monkeypatch, constant_numeric_agent(version, 1.0))
        result = await execute_experiment(store, run.id)

        assert result.status is RunStatus.FAILED
        spans = traced.exporter.exported_spans_as_dict()
        run_spans = [s for s in spans if s["name"] == "valcore.run"]
        assert len(run_spans) == 1
        assert run_spans[0]["end_time"] is not None
        assert run_spans[0]["attributes"]["status"] == RunStatus.FAILED.value
