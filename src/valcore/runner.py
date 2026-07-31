"""Execute an evaluator version over a dataset with bounded concurrency.

Results are persisted as each row finishes. Store calls are synchronous and are
run on worker threads so a slow write never blocks the event loop. A row's
ground-truth label is stored as a JSON object of the shape ``{"value": <score>}``.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.usage import RunUsage
from sqlmodel import select

from valcore.errors import ContractError
from valcore.factory import build_agent, extract_score, render_prompt
from valcore.metrics import compute_metrics
from valcore.models import (
    DatasetRow,
    EvaluatorVersion,
    Run,
    RunKind,
    RunResult,
    RunStatus,
    ScoreKind,
    check_dataset_compatibility,
)
from valcore.store import Store, session_scope


@dataclass
class RunEvent:
    """A progress event emitted during a run."""

    type: Literal["started", "row", "progress", "finished", "error"]
    run_id: str
    payload: dict


@dataclass
class _Outcome:
    """The in-memory result of scoring one row, used to shape its ``row`` event."""

    row_id: str
    success: bool
    predicted: str | float | None


def _label_value(row: DatasetRow) -> str | float | None:
    """Return the scalar ground-truth score from a row's ``{"value": ...}`` label."""
    if row.label is None:
        return None
    return row.label.get("value")


def _usage_dict(usage: RunUsage) -> dict:
    """Serialize agent usage into a plain dict for persistence."""
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "requests": usage.requests,
    }


def _agreement(
    score_kind: ScoreKind, predicted: str | float, label_value: str | float
) -> bool | float:
    """Agreement of a prediction with a label: exact match or signed numeric delta."""
    if score_kind is ScoreKind.CATEGORICAL:
        return predicted == label_value
    return float(predicted) - float(label_value)


def _clear_results(store: Store, run_id: str, row_ids: Sequence[str]) -> None:
    """Delete any existing results for the given rows so a retry replaces them."""
    with session_scope(store.engine) as session:
        existing = session.exec(
            select(RunResult).where(RunResult.run_id == run_id, RunResult.row_id.in_(list(row_ids)))
        )
        for result in existing:
            session.delete(result)


async def execute_run(
    store: Store,
    run_id: str,
    *,
    agent: Agent | None = None,
    on_event: Callable[[RunEvent], Awaitable[None]] | None = None,
    only_row_ids: Sequence[str] | None = None,
) -> Run:
    """Run an evaluator version over its dataset, persisting one result per row.

    Setup failures (incompatible dataset, missing labels, agent build) abort with
    status ``FAILED``. Per-row failures are recorded and never abort the run.
    """

    async def emit(kind: str, payload: dict) -> None:
        if on_event is not None:
            await on_event(RunEvent(type=kind, run_id=run_id, payload=payload))  # type: ignore[arg-type]

    run = await asyncio.to_thread(store.get_run, run_id)

    try:
        version = await asyncio.to_thread(store.get_version, run.version_id)
        dataset = await asyncio.to_thread(store.get_dataset, run.dataset_id)
        check_dataset_compatibility(version, dataset)

        all_rows = await asyncio.to_thread(store.list_rows, dataset.id)
        if only_row_ids is not None:
            wanted = set(only_row_ids)
            rows = [row for row in all_rows if row.id in wanted]
        else:
            rows = all_rows

        built_agent = agent if agent is not None else build_agent(version)
    except Exception as exc:  # noqa: BLE001 — any setup failure becomes a FAILED run
        failed = await asyncio.to_thread(
            store.update_run_status,
            run_id,
            RunStatus.FAILED,
            error=str(exc),
            finished_at=datetime.now(UTC),
        )
        await emit("error", {"error": str(exc)})
        return failed

    # The missing-label contract is a caller error, not a FAILED run: it must
    # propagate rather than be recorded on the run.
    if run.kind is RunKind.VALIDATION:
        unlabeled = sum(1 for row in rows if row.label is None)
        if unlabeled:
            raise ContractError(
                f"Validation run requires every row to carry a label; "
                f"{unlabeled} row(s) are unlabeled."
            )

    if only_row_ids is not None:
        await asyncio.to_thread(_clear_results, store, run_id, list(only_row_ids))

    await asyncio.to_thread(
        store.update_run_status, run_id, RunStatus.RUNNING, started_at=datetime.now(UTC)
    )
    await emit("started", {"total": len(rows)})

    want_agreement = run.kind is RunKind.VALIDATION
    semaphore = asyncio.Semaphore(run.concurrency)

    async def process(row: DatasetRow) -> _Outcome:
        try:
            outcome = await _score_row(store, run_id, version, built_agent, row, want_agreement)
            await emit(
                "row",
                {
                    "row_id": row.id,
                    "success": outcome.success,
                    "score_value": outcome.predicted,
                },
            )
            return outcome
        finally:
            semaphore.release()

    tasks: list[asyncio.Task[_Outcome]] = []
    cancelled = False
    for row in rows:
        await semaphore.acquire()
        current = await asyncio.to_thread(store.get_run, run_id)
        if current.cancel_requested:
            semaphore.release()
            cancelled = True
            break
        tasks.append(asyncio.create_task(process(row)))

    await asyncio.gather(*tasks)

    # Derive terminal status and metrics from every persisted result, not just
    # this batch's outcomes: a retry via ``only_row_ids`` must summarize the whole
    # run, whose store now holds both the replaced rows and the untouched ones.
    persisted = await asyncio.to_thread(store.list_results, run_id)
    any_error = any(result.error is not None for result in persisted)

    if cancelled:
        status = RunStatus.CANCELLED
    elif any_error:
        status = RunStatus.COMPLETED_WITH_ERRORS
    else:
        status = RunStatus.COMPLETED

    # A cancelled run's partial agreement would misrepresent the dataset, so
    # metrics are computed only for terminal states that ran to completion.
    metrics: dict | None = None
    if want_agreement and not cancelled:
        label_by_row = {row.id: _label_value(row) for row in all_rows}
        pairs = [
            (result.score_value, label_by_row.get(result.row_id))
            for result in persisted
            if result.error is None and label_by_row.get(result.row_id) is not None
        ]
        if pairs:
            labels = version.score_labels if version.score_kind is ScoreKind.CATEGORICAL else None
            metrics = compute_metrics(pairs, version.score_kind, labels)

    finished = await asyncio.to_thread(
        store.update_run_status,
        run_id,
        status,
        finished_at=datetime.now(UTC),
        metrics=metrics,
    )
    await emit("finished", {"status": status.value, "metrics": metrics})
    return finished


async def _score_row(
    store: Store,
    run_id: str,
    version: EvaluatorVersion,
    agent: Agent,
    row: DatasetRow,
    want_agreement: bool,
) -> _Outcome:
    """Score one row and persist its result; row failures are recorded, not raised."""
    label_value = _label_value(row) if want_agreement else None
    start = time.perf_counter()
    try:
        prompt = render_prompt(version, row.data)
        result = await agent.run(prompt)
        latency_ms = int((time.perf_counter() - start) * 1000)
        output: BaseModel = result.output
        score = extract_score(version, output)
        agreement = (
            _agreement(version.score_kind, score, label_value)
            if want_agreement and label_value is not None
            else None
        )
        await asyncio.to_thread(
            store.add_result,
            run_id,
            row_id=row.id,
            output=output.model_dump(mode="json"),
            score_value=score,
            agreement=agreement,
            latency_ms=latency_ms,
            usage=_usage_dict(result.usage),
        )
        return _Outcome(row.id, True, score)
    except Exception as exc:  # noqa: BLE001 — a row failure is recorded, never fatal
        latency_ms = int((time.perf_counter() - start) * 1000)
        await asyncio.to_thread(
            store.add_result,
            run_id,
            row_id=row.id,
            output=None,
            error=str(exc),
            latency_ms=latency_ms,
        )
        return _Outcome(row.id, False, None)
