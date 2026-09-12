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

from valcore import tracing
from valcore.errors import ContractError
from valcore.factory import build_agent, extract_score, render_prompt
from valcore.metrics import compute_metrics
from valcore.models import (
    Annotation,
    DatasetRow,
    EvaluatorVersion,
    LabelSet,
    Run,
    RunKind,
    RunResult,
    RunStatus,
    ScoreKind,
    annotation_ground_truth,
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
        label_sets = await asyncio.to_thread(store.list_label_sets, dataset.id)
        # kind matters: an EVAL run never compares predictions to ground truth, so its label
        # space is free to differ from every label set's. Only VALIDATION requires a match.
        matched_label_set: LabelSet | None = check_dataset_compatibility(
            version, dataset, label_sets, kind=run.kind
        )

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

    # A VALIDATION run scores only rows with valid ground truth from the matched label
    # set; this is a caller error (not a FAILED run) only when NONE qualify, since there
    # would be nothing to validate. Rows lacking a label are silently excluded, not an
    # error -- unlike the old all-or-nothing contract.
    ground_truth_by_row: dict[str, str | float | None] = {}
    if run.kind is RunKind.VALIDATION and matched_label_set is not None:
        # Ground truth is looked up over ``all_rows``, not just this call's (possibly
        # ``only_row_ids``-narrowed) ``rows``: a retry must summarize the whole run, so the
        # metrics block below needs every row's ground truth, not only the retried subset's.
        annotations: list[Annotation] = await asyncio.to_thread(
            store.list_annotations_for_rows, matched_label_set.id, [row.id for row in all_rows]
        )
        annotations_by_row = {a.dataset_row_id: a for a in annotations}
        ground_truth_by_row = {
            row.id: annotation_ground_truth(matched_label_set, annotations_by_row.get(row.id))
            for row in all_rows
        }
    if run.kind is RunKind.VALIDATION:
        rows = [row for row in rows if ground_truth_by_row.get(row.id) is not None]
        if not rows:
            detail = (
                f" for the matched label set {matched_label_set.name!r}"
                if matched_label_set
                else ""
            )
            raise ContractError(
                f"Validation run requires at least one row with a valid label{detail}; none found."
            )

    with tracing.run_span(run, version, dataset, row_count=len(rows)) as span:
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
                outcome = await _score_row(
                    store, run_id, version, built_agent, row, ground_truth_by_row.get(row.id)
                )
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
            # Hand control to the event loop once so the task just created actually starts
            # (reaches its own first await) before this loop moves on to the next row.
            # Without this, whether concurrency is fully utilized depends on incidental
            # scheduling behavior of the `to_thread` call above, which differs across event
            # loop implementations (e.g. Windows' default ProactorEventLoop schedules
            # to_thread completions differently than the SelectorEventLoop used elsewhere) --
            # `run.concurrency` should be honored the same way regardless of platform.
            await asyncio.sleep(0)

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
            pairs = [
                (result.score_value, ground_truth_by_row.get(result.row_id))
                for result in persisted
                if result.error is None and ground_truth_by_row.get(result.row_id) is not None
            ]
            if pairs:
                labels = (
                    version.score_labels if version.score_kind is ScoreKind.CATEGORICAL else None
                )
                metrics = compute_metrics(pairs, version.score_kind, labels)

        finished = await asyncio.to_thread(
            store.update_run_status,
            run_id,
            status,
            finished_at=datetime.now(UTC),
            metrics=metrics,
        )
        span.set_attribute("status", status.value)
        for key, value in (metrics or {}).items():
            span.set_attribute(key, value)
        await emit("finished", {"status": status.value, "metrics": metrics})
        return finished


async def _score_row(
    store: Store,
    run_id: str,
    version: EvaluatorVersion,
    agent: Agent,
    row: DatasetRow,
    label_value: str | float | None,
) -> _Outcome:
    """Score one row and persist its result; row failures are recorded, not raised."""
    with tracing.row_span(row):
        start = time.perf_counter()
        try:
            prompt = render_prompt(version, row.data)
            result = await agent.run(prompt)
            latency_ms = int((time.perf_counter() - start) * 1000)
            output: BaseModel = result.output
            score = extract_score(version, output)
            agreement = (
                _agreement(version.score_kind, score, label_value)
                if label_value is not None
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
