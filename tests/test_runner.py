"""Tests for the eval runner: bounded-concurrency execution over a dataset.

No network: agent behavior is driven by ``FunctionModel``/``TestModel`` agents
injected via ``agent=``, with a real ``Store`` on a ``tmp_path`` SQLite DB.
"""

import asyncio

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
def store(tmp_path) -> Store:
    """A real Store backed by a fresh SQLite DB under tmp_path."""
    engine = create_engine(tmp_path / "eval.db")
    init_db(engine)
    return Store(engine)


def make_version(store: Store, **overrides):
    """Create an evaluator and a valid version, returning the version."""
    evaluator = store.create_evaluator("ev")
    fields = {**VERSION_FIELDS, **overrides}
    return store.create_version(evaluator.id, **fields)


def make_dataset(
    store: Store,
    labels: list[str | None],
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
    """An agent whose model always emits the given verdict via the output tool."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"verdict": verdict})])

    return Agent(FunctionModel(respond), output_type=build_output_model(version))


async def collect_events(events: list[RunEvent], event: RunEvent) -> None:
    """on_event callback that records every event."""
    events.append(event)


# -- Happy path ---------------------------------------------------------------


@pytest.mark.anyio
async def test_validation_happy_path(store: Store) -> None:
    version = make_version(store)
    dataset = make_dataset(store, ["pass", "fail", "pass", "fail", "pass"])
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=3)

    result = await execute_run(store, run.id, agent=constant_agent(version))

    assert result.status is RunStatus.COMPLETED
    results = store.list_results(run.id)
    assert len(results) == 5
    # Predictions are all "pass"; agreement is exact match against each label.
    by_row = {r.row_id: r for r in results}
    rows = store.list_rows(dataset.id)
    expected = [True, False, True, False, True]
    for row, exp in zip(rows, expected, strict=True):
        assert by_row[row.id].agreement is exp
        assert by_row[row.id].score_value == "pass"
        assert by_row[row.id].error is None
    assert result.metrics is not None
    assert result.metrics["n"] == 5
    assert result.metrics["accuracy"] == pytest.approx(3 / 5)


# -- Per-row failure ----------------------------------------------------------


@pytest.mark.anyio
async def test_row_failure_does_not_abort(store: Store) -> None:
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

    result = await execute_run(store, run.id, agent=agent)

    assert result.status is RunStatus.COMPLETED_WITH_ERRORS
    results = store.list_results(run.id)
    assert len(results) == 5
    errored = [r for r in results if r.error is not None]
    assert len(errored) == 1
    assert "boom on third row" in errored[0].error
    assert errored[0].output is None
    # Metrics computed over the 4 successes only.
    assert result.metrics is not None
    assert result.metrics["n"] == 4


# -- Eval run -----------------------------------------------------------------


@pytest.mark.anyio
async def test_eval_run_unlabeled_no_metrics(store: Store) -> None:
    version = make_version(store)
    dataset = make_dataset(store, [None, None, None])
    run = store.create_run(RunKind.EVAL, version.id, dataset.id, concurrency=2)

    result = await execute_run(store, run.id, agent=constant_agent(version))

    assert result.status is RunStatus.COMPLETED
    assert result.metrics is None
    results = store.list_results(run.id)
    assert len(results) == 3
    assert all(r.agreement is None for r in results)
    assert all(r.error is None for r in results)


# -- Validation label contract ------------------------------------------------


@pytest.mark.anyio
async def test_validation_partial_labels_raises(store: Store) -> None:
    version = make_version(store)
    dataset = make_dataset(store, ["pass", None, "fail", None])
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=2)

    with pytest.raises(ContractError) as exc_info:
        await execute_run(store, run.id, agent=constant_agent(version))

    assert "2" in str(exc_info.value)
    assert store.list_results(run.id) == []


# -- Incompatible dataset -----------------------------------------------------


@pytest.mark.anyio
async def test_incompatible_dataset_fails(store: Store) -> None:
    version = make_version(store)
    # Dataset missing the required "output" column.
    dataset = make_dataset(store, ["pass", "fail"], columns=["input"])
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=2)

    result = await execute_run(store, run.id, agent=constant_agent(version))

    assert result.status is RunStatus.FAILED
    assert result.error is not None
    assert "output" in result.error
    assert store.list_results(run.id) == []


# -- Cancellation -------------------------------------------------------------


@pytest.mark.anyio
async def test_cancellation_mid_run(store: Store) -> None:
    version = make_version(store)
    dataset = make_dataset(store, ["pass", "pass", "pass", "pass", "pass"])
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=1)

    async def on_event(event: RunEvent) -> None:
        # Cancel as soon as the first row completes.
        if event.type == "row":
            store.request_cancel(run.id)

    result = await execute_run(store, run.id, agent=constant_agent(version), on_event=on_event)

    assert result.status is RunStatus.CANCELLED
    results = store.list_results(run.id)
    assert 0 < len(results) < 5
    # No result is half-written: each either has an output or an error, never
    # partial, and latency was recorded.
    for r in results:
        assert (r.output is not None) or (r.error is not None)


# -- Bounded concurrency ------------------------------------------------------


@pytest.mark.anyio
async def test_concurrency_is_bounded(store: Store) -> None:
    version = make_version(store)
    dataset = make_dataset(store, ["pass"] * 6)
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=2)

    state = {"inflight": 0, "peak": 0}

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        state["inflight"] += 1
        state["peak"] = max(state["peak"], state["inflight"])
        await asyncio.sleep(0.02)
        state["inflight"] -= 1
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"verdict": "pass"})])

    agent = Agent(FunctionModel(respond), output_type=build_output_model(version))

    result = await execute_run(store, run.id, agent=agent)

    assert result.status is RunStatus.COMPLETED
    assert len(store.list_results(run.id)) == 6
    assert state["peak"] <= 2
    assert state["peak"] >= 2  # concurrency actually exercised


# -- only_row_ids replaces results --------------------------------------------


@pytest.mark.anyio
async def test_only_row_ids_replaces_results(store: Store) -> None:
    version = make_version(store)
    dataset = make_dataset(store, ["pass", "fail", "pass", "fail", "pass"])
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=2)

    await execute_run(store, run.id, agent=constant_agent(version))
    first = store.list_results(run.id)
    assert len(first) == 5
    first_ids = {r.id for r in first}

    rows = store.list_rows(dataset.id)
    retry_ids = [rows[0].id, rows[2].id]

    retried = await execute_run(
        store, run.id, agent=constant_agent(version), only_row_ids=retry_ids
    )

    second = store.list_results(run.id)
    # Count unchanged: the two rows were replaced, not appended.
    assert len(second) == 5
    assert {r.row_id for r in second} == {r.id for r in rows}
    # The two retried rows have brand-new result records.
    replaced = {r.id for r in second if r.row_id in set(retry_ids)}
    assert replaced.isdisjoint(first_ids)
    # Status and metrics summarize the whole run, not just the retried subset.
    assert retried.status is RunStatus.COMPLETED
    assert retried.metrics is not None
    assert retried.metrics["n"] == 5
    assert retried.metrics["accuracy"] == pytest.approx(3 / 5)


@pytest.mark.anyio
async def test_retry_fixes_failed_row_updates_run_summary(store: Store) -> None:
    version = make_version(store)
    dataset = make_dataset(store, ["pass", "pass", "pass", "pass", "pass"])
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=1)

    calls = [0]

    def failing_third(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls[0] += 1
        if calls[0] == 3:
            raise RuntimeError("boom on third row")
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"verdict": "pass"})])

    first = await execute_run(
        store,
        run.id,
        agent=Agent(FunctionModel(failing_third), output_type=build_output_model(version)),
    )
    assert first.status is RunStatus.COMPLETED_WITH_ERRORS
    assert first.metrics["n"] == 4

    failed_row_id = store.failed_result_row_ids(run.id)[0]

    # Retry only the previously failed row; it now succeeds. The run summary must
    # reflect all five rows, not just the one re-executed.
    retried = await execute_run(
        store, run.id, agent=constant_agent(version), only_row_ids=[failed_row_id]
    )
    assert retried.status is RunStatus.COMPLETED
    assert retried.metrics is not None
    assert retried.metrics["n"] == 5
    assert len([r for r in store.list_results(run.id) if r.error is not None]) == 0


# -- Events -------------------------------------------------------------------


@pytest.mark.anyio
async def test_events_emitted_once_each(store: Store) -> None:
    version = make_version(store)
    dataset = make_dataset(store, ["pass", "fail", "pass"])
    run = store.create_run(RunKind.VALIDATION, version.id, dataset.id, concurrency=2)

    events: list[RunEvent] = []

    async def on_event(event: RunEvent) -> None:
        events.append(event)

    await execute_run(store, run.id, agent=constant_agent(version), on_event=on_event)

    kinds = [e.type for e in events]
    assert kinds.count("started") == 1
    assert kinds.count("finished") == 1
    assert kinds.count("row") == 3
    # started carries the total row count.
    started = next(e for e in events if e.type == "started")
    assert started.payload["total"] == 3
