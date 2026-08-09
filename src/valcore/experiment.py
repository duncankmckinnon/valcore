"""Second execution mode for scoring an evaluator version: ``pydantic_evals.Dataset.evaluate``.

``runner.execute_run`` stays the primary engine -- it keeps cancellation and row-subset
retry, which ``evaluate()`` cannot express. This module trades those for what
``pydantic_evals`` gives for free: concurrency, retries, and (via ``spec.dataset_to_evals``
and ``tracing``) a shape Logfire's experiment view can render directly.

The task/evaluator mapping here is the inverse of the *exported* package: there the
consumer's task is measured by a ``ValcoreJudge`` evaluator; here the judge itself is the
task being measured, and agreement with the human label is the evaluator. Both engines
call ``metrics.compute_metrics`` on the same kind of ``(predicted, label)`` pairs read back
from persisted ``RunResult`` rows, never from ``pydantic_evals``' own evaluator output --
that is what keeps a run and an experiment over the same data reporting identical numbers.
"""

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic_ai import Agent
from pydantic_evals import Case, CaseLifecycle, increment_eval_metric, set_eval_attribute
from pydantic_evals.evaluators import Evaluator, EvaluatorContext
from pydantic_evals.evaluators.common import EqualsExpected
from pydantic_evals.reporting import ReportCase, ReportCaseFailure

from valcore.factory import build_agent, extract_score, render_prompt
from valcore.metrics import compute_metrics
from valcore.models import (
    DatasetRow,
    EvaluatorVersion,
    Run,
    RunKind,
    RunStatus,
    ScoreKind,
    check_dataset_compatibility,
)
from valcore.runner import RunEvent, _agreement, _label_value
from valcore.spec import dataset_to_evals
from valcore.store import Store
from valcore.tracing import row_span, run_span

# Set by ``PersistResults.setup`` and read inside the task so the Gateway's per-call span
# nests under the right ``valcore.score_row`` span. Safe under concurrency: ``evaluate()``
# schedules each case as its own asyncio task, so each gets an independent copy of the
# context created at that point -- setting this in one case's ``setup`` never leaks into
# another case running at the same time.
_current_row: ContextVar[DatasetRow | None] = ContextVar("_experiment_current_row", default=None)

_USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens", "requests")


@dataclass(repr=False)
class NumericDelta(Evaluator[object, object, object]):
    """Signed delta of the task's output against the expected label.

    ``EqualsExpected`` is exact-match, which is wrong for numeric scores where a "close"
    prediction should not fail identically to a wildly off one. Delegates to
    ``runner._agreement`` rather than reimplementing it, so this can never drift from what
    the runner engine reports for the same pair.
    """

    def evaluate(self, ctx: EvaluatorContext[object, object, object]) -> float:
        """Return ``predicted - label``, matching ``runner._agreement``'s numeric branch."""
        return _agreement(ScoreKind.NUMERIC, ctx.output, ctx.expected_output)


def _usage_from_metrics(metrics: dict[str, float | int]) -> dict | None:
    """Reassemble a usage dict from the per-case metrics the task recorded, if complete."""
    if not all(key in metrics for key in _USAGE_KEYS):
        return None
    return {key: metrics[key] for key in _USAGE_KEYS}


def _make_task(version: EvaluatorVersion, agent: Agent) -> Callable[[dict], Awaitable[str | float]]:
    """Build the ``pydantic_evals`` task: the same three calls ``runner._score_row`` makes.

    Returns the score alone -- not the full structured output -- because the agreement
    evaluators (``EqualsExpected``/``NumericDelta``) compare ``ctx.output`` directly against
    the human label. The full output and token usage are not lost: they are recorded via
    ``set_eval_attribute``/``increment_eval_metric`` so ``PersistResults.teardown`` can
    still persist them on ``RunResult``.
    """

    async def task(inputs: dict) -> str | float:
        row = _current_row.get()
        with row_span(row):
            prompt = render_prompt(version, inputs)
            result = await agent.run(prompt)
            output = result.output
            score = extract_score(version, output)
            set_eval_attribute("output", output.model_dump(mode="json"))
            usage = result.usage
            increment_eval_metric("input_tokens", usage.input_tokens)
            increment_eval_metric("output_tokens", usage.output_tokens)
            increment_eval_metric("total_tokens", usage.total_tokens)
            increment_eval_metric("requests", usage.requests)
        return score

    return task


class PersistResults(CaseLifecycle):
    """The persistence seam: writes one ``RunResult`` and emits one ``row`` event per case.

    A new instance is created per case by ``execute_experiment``'s lifecycle factory, which
    is what supplies the extra context (store, run, version) the bare ``CaseLifecycle``
    contract does not carry.
    """

    def __init__(
        self,
        case: Case,
        *,
        store: Store,
        run_id: str,
        version: EvaluatorVersion,
        want_agreement: bool,
        rows_by_id: dict[str, DatasetRow],
        emit_row: Callable[[str, bool, str | float | None], Awaitable[None]],
    ) -> None:
        super().__init__(case)
        self._store = store
        self._run_id = run_id
        self._version = version
        self._want_agreement = want_agreement
        self._emit_row = emit_row
        self._row = rows_by_id[case.name]

    async def setup(self) -> None:
        """Publish this case's row so the task can open the matching ``valcore.score_row`` span."""
        _current_row.set(self._row)

    async def teardown(self, result: ReportCase | ReportCaseFailure | None) -> None:
        """Persist the case's outcome and emit its ``row`` event.

        ``result`` is ``None`` when the evaluation was interrupted before a report object
        existed for this case; that is recorded as an errored result rather than raised, so
        a single interruption cannot leave a case with no ``RunResult`` at all.
        """
        row_id = self._row.id

        if result is None or isinstance(result, ReportCaseFailure):
            error = (
                result.error_message
                if isinstance(result, ReportCaseFailure)
                else "Case was interrupted before it could complete."
            )
            await asyncio.to_thread(
                self._store.add_result, self._run_id, row_id=row_id, output=None, error=error
            )
            await self._emit_row(row_id, False, None)
            return

        score = result.output
        agreement = None
        if self._want_agreement and result.expected_output is not None:
            agreement = _agreement(self._version.score_kind, score, result.expected_output)

        await asyncio.to_thread(
            self._store.add_result,
            self._run_id,
            row_id=row_id,
            output=result.attributes.get("output"),
            score_value=score,
            agreement=agreement,
            latency_ms=int(result.task_duration * 1000),
            usage=_usage_from_metrics(result.metrics),
        )
        await self._emit_row(row_id, True, score)


async def execute_experiment(
    store: Store,
    run_id: str,
    *,
    on_event: Callable[[RunEvent], Awaitable[None]] | None = None,
) -> Run:
    """Run an evaluator version over its dataset via ``pydantic_evals.Dataset.evaluate``.

    Mirrors ``runner.execute_run``'s contract -- setup failures (incompatible dataset,
    agent build) abort with status ``FAILED`` before any result is written; per-case
    failures are recorded and never abort the run. Unlike the runner, there is no
    cancellation: ``evaluate()`` has none, so none is polled for here.
    """

    async def emit(kind: str, payload: dict) -> None:
        if on_event is not None:
            await on_event(RunEvent(type=kind, run_id=run_id, payload=payload))  # type: ignore[arg-type]

    async def emit_row(row_id: str, success: bool, score_value: str | float | None) -> None:
        await emit("row", {"row_id": row_id, "success": success, "score_value": score_value})

    run = await asyncio.to_thread(store.get_run, run_id)

    try:
        version = await asyncio.to_thread(store.get_version, run.version_id)
        dataset = await asyncio.to_thread(store.get_dataset, run.dataset_id)
        check_dataset_compatibility(version, dataset)
        rows = await asyncio.to_thread(store.list_rows, dataset.id)
        agent = build_agent(version)
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

    await asyncio.to_thread(
        store.update_run_status, run_id, RunStatus.RUNNING, started_at=datetime.now(UTC)
    )
    await emit("started", {"total": len(rows)})

    # An EVAL-kind run has no labels to agree with, mirroring ``runner``'s ``want_agreement``.
    want_agreement = run.kind is RunKind.VALIDATION
    evaluators: list[Evaluator] = []
    if want_agreement:
        evaluators.append(
            EqualsExpected() if version.score_kind is ScoreKind.CATEGORICAL else NumericDelta()
        )

    evals_dataset = dataset_to_evals(dataset, rows, evaluators)
    task = _make_task(version, agent)
    rows_by_id = {row.id: row for row in rows}

    def lifecycle_factory(case: Case) -> PersistResults:
        return PersistResults(
            case,
            store=store,
            run_id=run_id,
            version=version,
            want_agreement=want_agreement,
            rows_by_id=rows_by_id,
            emit_row=emit_row,
        )

    with run_span(run, version, dataset, len(rows)) as span:
        report = await evals_dataset.evaluate(
            task,
            name=version.version_name,
            max_concurrency=run.concurrency,
            progress=False,
            lifecycle=lifecycle_factory,
        )

        # Derived from every persisted result, exactly as ``runner.execute_run`` does, so
        # the two engines can never disagree about the run's terminal status or metrics.
        persisted = await asyncio.to_thread(store.list_results, run_id)
        any_error = any(result.error is not None for result in persisted)
        status = RunStatus.COMPLETED_WITH_ERRORS if any_error else RunStatus.COMPLETED

        metrics: dict | None = None
        if want_agreement:
            label_by_row = {row.id: _label_value(row) for row in rows}
            pairs = [
                (result.score_value, label_by_row.get(result.row_id))
                for result in persisted
                if result.error is None and label_by_row.get(result.row_id) is not None
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
        # Marks this run as experiment-produced, which is what makes ``request_cancel``
        # fail honestly for it -- ``evaluate()`` has no cancellation hook to honor.
        await asyncio.to_thread(
            store.set_experiment,
            run_id,
            experiment_name=version.version_name,
            case_count=len(report.cases),
        )

        span.set_attribute("status", status.value)
        if metrics is not None:
            for key, value in metrics.items():
                span.set_attribute(key, value)

    await emit("finished", {"status": status.value, "metrics": metrics})
    return finished
