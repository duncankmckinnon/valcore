"""Run lifecycle routes: start, stream, inspect, cancel, retry, and compare runs.

Runs execute in the background as ``asyncio`` tasks whose progress is fanned out to
SSE subscribers over the process-wide :data:`valcore.api.events.bus`. A module-level registry
of in-flight tasks lets cancellation be honored and lets the app await them on shutdown.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent
from sse_starlette.sse import EventSourceResponse

from valcore import config
from valcore.api.deps import get_store
from valcore.api.events import bus
from valcore.errors import ContractError
from valcore.experiment import execute_experiment
from valcore.models import EvaluatorVersion, RunKind, RunStatus, check_dataset_compatibility
from valcore.runner import RunEvent, execute_run
from valcore.store import Store

router = APIRouter(prefix="/api/runs", tags=["runs"])

StoreDep = Annotated[Store, Depends(get_store)]

AgentFactory = Callable[[EvaluatorVersion], Agent]

_DEFAULT_CONCURRENCY = 4
_TERMINAL_STATUSES: frozenset[RunStatus] = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.COMPLETED_WITH_ERRORS,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
    }
)

# In-flight background run tasks, keyed by run id, so cancel can be honored and the
# app can await them on shutdown.
_tasks: dict[str, asyncio.Task] = {}


def get_agent_factory() -> AgentFactory | None:
    """Return ``None`` so the runner builds the real gateway agent.

    Tests override this dependency to inject a ``TestModel``-backed agent.
    """
    return None


AgentFactoryDep = Annotated[AgentFactory | None, Depends(get_agent_factory)]


# -- Request bodies -----------------------------------------------------------


class RunCreate(BaseModel):
    """Payload to start a run over a dataset with a chosen evaluator version."""

    kind: RunKind
    version_id: str
    dataset_id: str
    concurrency: int | None = None
    # Which engine executes the run. Orthogonal to ``kind`` -- an experiment can be either EVAL
    # or VALIDATION -- which is why it is a separate field rather than a third ``RunKind``.
    # ``experiment`` drives ``pydantic_evals.Dataset.evaluate``, so the run appears in Logfire's
    # experiments view; it cannot be cancelled, because ``evaluate()`` has no cancellation.
    experiment: bool = False


# -- Response bodies ----------------------------------------------------------


class RunOut(BaseModel):
    """A run with its status and metrics as returned to the client."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    kind: RunKind
    version_id: str
    dataset_id: str
    status: RunStatus
    concurrency: int
    started_at: datetime | None
    finished_at: datetime | None
    metrics: dict | None
    error: str | None
    cancel_requested: bool


class ResultRow(BaseModel):
    """A single run result joined with the data and human label of its row."""

    result_id: str
    row_id: str
    idx: int
    data: dict
    output: dict | None
    score_value: str | float | None
    agreement: bool | float | None
    error: str | None
    latency_ms: int | None
    label: str | float | None


class ResultsPage(BaseModel):
    """A paginated, filtered slice of a run's results."""

    results: list[ResultRow]
    total: int
    limit: int
    offset: int


class CompareRow(BaseModel):
    """One dataset row scored by both compared runs, with the human label."""

    row_id: str
    idx: int
    data: dict
    output_a: dict | None
    output_b: dict | None
    score_a: str | float | None
    score_b: str | float | None
    label: str | float | None
    disagree: bool


class CompareOut(BaseModel):
    """Side-by-side comparison of two runs over the same dataset."""

    run_a: RunOut
    run_b: RunOut
    metrics_delta: dict[str, float]
    rows: list[CompareRow]


# -- Background execution -----------------------------------------------------


async def _run_to_completion(
    store: Store,
    run_id: str,
    agent_factory: AgentFactory | None,
    only_row_ids: list[str] | None,
    experiment: bool = False,
) -> None:
    """Execute a run to completion, publishing events and never dying silently.

    Any exception escaping the runner is recorded on the run as ``FAILED`` so a run
    is never left stuck in ``RUNNING``. The bus is always closed so subscribers stop.

    Guards on ``config.require_gateway_key()`` before ever building an agent or calling
    ``execute_run``: ``defer_model_check=True`` lets ``build_agent`` succeed with no key,
    so without this a keyless run would instead fail once per row inside ``_score_row``.
    """

    async def on_event(event: RunEvent) -> None:
        bus.publish(run_id, {"type": event.type, "run_id": run_id, "payload": event.payload})

    try:
        config.require_gateway_key()
        agent: Agent | None = None
        if agent_factory is not None:
            run = await asyncio.to_thread(store.get_run, run_id)
            version = await asyncio.to_thread(store.get_version, run.version_id)
            agent = agent_factory(version)
        if experiment:
            # The experiment engine builds its own agent and has no row-subset retry, so
            # neither `agent` nor `only_row_ids` applies here.
            await execute_experiment(store, run_id, on_event=on_event)
        else:
            await execute_run(
                store, run_id, agent=agent, on_event=on_event, only_row_ids=only_row_ids
            )
    except Exception as exc:  # noqa: BLE001 — a dying task must mark the run FAILED
        await asyncio.to_thread(
            store.update_run_status,
            run_id,
            RunStatus.FAILED,
            error=str(exc),
            finished_at=datetime.now(UTC),
        )
        bus.publish(run_id, {"type": "error", "run_id": run_id, "payload": {"error": str(exc)}})
    finally:
        bus.close(run_id)


def _launch_run(
    store: Store,
    run_id: str,
    agent_factory: AgentFactory | None,
    only_row_ids: list[str] | None = None,
    experiment: bool = False,
) -> None:
    """Register and launch a background run task, cleaning up the registry on completion."""
    task = asyncio.create_task(
        _run_to_completion(store, run_id, agent_factory, only_row_ids, experiment)
    )
    _tasks[run_id] = task
    task.add_done_callback(lambda _t: _tasks.pop(run_id, None))


# -- Result filtering ---------------------------------------------------------


def _is_disagreement(agreement: bool | float | None) -> bool:
    """Return True when a result disagrees with its label (mismatch or nonzero delta)."""
    if agreement is None:
        return False
    if isinstance(agreement, bool):
        return agreement is False
    return float(agreement) != 0.0


def _label_value(label: dict | None) -> str | float | None:
    """Return the scalar value from a row's ``{"value": ...}`` label, if any."""
    if label is None:
        return None
    return label.get("value")


def _metrics_delta(a: dict | None, b: dict | None) -> dict[str, float]:
    """Return ``b - a`` for every scalar numeric metric shared by both runs."""
    if not a or not b:
        return {}
    delta: dict[str, float] = {}
    for key in a.keys() & b.keys():
        av, bv = a[key], b[key]
        numeric = (
            isinstance(av, int | float)
            and not isinstance(av, bool)
            and isinstance(bv, int | float)
            and not isinstance(bv, bool)
        )
        if numeric:
            delta[key] = float(bv) - float(av)
    return delta


# -- Endpoints ----------------------------------------------------------------


@router.post("", response_model=RunOut)
async def create_run(body: RunCreate, store: StoreDep, agent_factory: AgentFactoryDep) -> RunOut:
    """Create a run (freezing its version) and launch it in the background.

    Returns immediately with status ``PENDING``; the run is never awaited in the handler.

    Guards on ``config.require_gateway_key()`` before ``store.create_run``: a missing key
    must surface synchronously as a ``ConfigError``, with no run ever persisted, rather than
    as an asynchronous ``FAILED`` transition discovered by polling.

    Compatibility is checked here for the same reason. ``execute_run`` checks it too, but only
    after the run exists, so an incompatible pairing -- most often a VALIDATION run whose
    evaluator prescribes its own label space -- would otherwise persist a ``FAILED`` run that
    the user has to go and read. Passing ``body.kind`` keeps an EVAL run permitted when the
    label spaces differ, which is the whole point of prescribing them.
    """
    config.require_gateway_key()
    check_dataset_compatibility(
        store.get_version(body.version_id),
        store.get_dataset(body.dataset_id),
        kind=body.kind,
    )
    run = store.create_run(
        kind=body.kind,
        version_id=body.version_id,
        dataset_id=body.dataset_id,
        concurrency=body.concurrency or _DEFAULT_CONCURRENCY,
    )
    _launch_run(store, run.id, agent_factory, experiment=body.experiment)
    return RunOut.model_validate(run)


@router.get("", response_model=list[RunOut])
async def list_runs(
    store: StoreDep, version_id: str | None = None, dataset_id: str | None = None
) -> list[RunOut]:
    """List runs, optionally filtered by version and/or dataset."""
    runs = store.list_runs(version_id=version_id, dataset_id=dataset_id)
    return [RunOut.model_validate(run) for run in runs]


@router.get("/compare", response_model=CompareOut)
async def compare_runs(a: str, b: str, store: StoreDep) -> CompareOut:
    """Compare two runs over the same dataset, disagreements first.

    Rejects runs that target different datasets with a 422 ``ContractError``.
    """
    run_a = store.get_run(a)
    run_b = store.get_run(b)
    if run_a.dataset_id != run_b.dataset_id:
        raise ContractError(
            "Runs target different datasets and cannot be compared "
            f"({run_a.dataset_id!r} vs {run_b.dataset_id!r})."
        )

    results_a = {r.row_id: r for r in store.list_results(a)}
    results_b = {r.row_id: r for r in store.list_results(b)}

    rows: list[CompareRow] = []
    for row in store.list_rows(run_a.dataset_id):
        ra = results_a.get(row.id)
        rb = results_b.get(row.id)
        score_a = ra.score_value if ra is not None else None
        score_b = rb.score_value if rb is not None else None
        rows.append(
            CompareRow(
                row_id=row.id,
                idx=row.idx,
                data=row.data,
                output_a=ra.output if ra is not None else None,
                output_b=rb.output if rb is not None else None,
                score_a=score_a,
                score_b=score_b,
                label=_label_value(row.label),
                disagree=score_a != score_b,
            )
        )

    rows.sort(key=lambda r: (not r.disagree, r.idx))

    return CompareOut(
        run_a=RunOut.model_validate(run_a),
        run_b=RunOut.model_validate(run_b),
        metrics_delta=_metrics_delta(run_a.metrics, run_b.metrics),
        rows=rows,
    )


@router.get("/{id}", response_model=RunOut)
async def get_run(id: str, store: StoreDep) -> RunOut:
    """Return a single run with its status and metrics."""
    return RunOut.model_validate(store.get_run(id))


@router.get("/{id}/results", response_model=ResultsPage)
async def list_run_results(
    id: str,
    store: StoreDep,
    only_disagreements: bool = False,
    only_errors: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> ResultsPage:
    """Return a paginated slice of a run's results joined with their row data."""
    run = store.get_run(id)
    rows_by_id = {row.id: row for row in store.list_rows(run.dataset_id)}

    kept: list[ResultRow] = []
    for result in store.list_results(id):
        is_error = result.error is not None
        is_disagreement = _is_disagreement(result.agreement)
        if only_disagreements and only_errors:
            keep = is_error or is_disagreement
        elif only_disagreements:
            keep = is_disagreement
        elif only_errors:
            keep = is_error
        else:
            keep = True
        if not keep:
            continue
        row = rows_by_id.get(result.row_id)
        kept.append(
            ResultRow(
                result_id=result.id,
                row_id=result.row_id,
                idx=row.idx if row is not None else -1,
                data=row.data if row is not None else {},
                output=result.output,
                score_value=result.score_value,
                agreement=result.agreement,
                error=result.error,
                latency_ms=result.latency_ms,
                label=_label_value(row.label) if row is not None else None,
            )
        )

    total = len(kept)
    page = kept[offset : offset + limit]
    return ResultsPage(results=page, total=total, limit=limit, offset=offset)


@router.get("/{id}/events")
async def run_events(id: str, store: StoreDep) -> EventSourceResponse:
    """Stream run progress as SSE, replaying current status before live events.

    On connect the current status and completed-row count are replayed so a late
    subscriber (e.g. a page refresh) is not stuck at 0%. If the run is already in a
    terminal state, a final ``finished`` event is emitted and the stream closes.
    """
    store.get_run(id)

    async def event_stream() -> AsyncIterator[dict]:
        run = await asyncio.to_thread(store.get_run, id)
        completed = len(await asyncio.to_thread(store.list_results, id))
        yield {
            "event": "status",
            "data": json.dumps(
                {"status": run.status.value, "completed": completed, "metrics": run.metrics}
            ),
        }
        if run.status in _TERMINAL_STATUSES:
            yield {
                "event": "finished",
                "data": json.dumps({"status": run.status.value, "metrics": run.metrics}),
            }
            return
        async for event in bus.subscribe(id):
            yield {"event": event["type"], "data": json.dumps(event["payload"])}
            if event["type"] in ("finished", "error"):
                return

    return EventSourceResponse(event_stream())


@router.post("/{id}/cancel", response_model=RunOut)
async def cancel_run(id: str, store: StoreDep) -> RunOut:
    """Flag a run for cancellation; the background task transitions it to ``CANCELLED``."""
    return RunOut.model_validate(store.request_cancel(id))


@router.post("/{id}/retry-failed", response_model=RunOut)
async def retry_failed(id: str, store: StoreDep, agent_factory: AgentFactoryDep) -> RunOut:
    """Re-run only the rows that failed in a run, replacing their results.

    The run is reset to ``PENDING`` before relaunching so a client polling status is
    never fooled by the previous run's stale terminal state.

    Guards on ``config.require_gateway_key()`` before any other work: a missing key
    must surface synchronously as a ``ConfigError``, with the prior run's status and
    results untouched and no background task launched.
    """
    config.require_gateway_key()
    store.get_run(id)
    failed_row_ids = store.failed_result_row_ids(id)
    run = store.update_run_status(id, RunStatus.PENDING, error=None, finished_at=None)
    _launch_run(store, id, agent_factory, only_row_ids=failed_row_ids)
    return RunOut.model_validate(run)
