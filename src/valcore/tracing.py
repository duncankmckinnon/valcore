"""Logfire span shaping: the only module that talks to Logfire's instrumentation surface.

The Pydantic AI Gateway already reports every LLM call server-side and injects a
W3C ``traceparent`` header into each request, so its spans nest under whatever
local span is active. valcore supplies the parent context (``valcore.run`` /
``valcore.score_row``) that gives the Gateway's spans structure, and additionally
instruments Pydantic AI so the agent run, its tool calls, and harness-capability
activity appear too -- none of which the Gateway can see, since they never leave
the process. The trade is that the model request itself is reported twice, once by
each side; if that duplication is unwanted, drop the ``instrument_pydantic_ai``
call in :func:`configure_tracing` and only the local detail is lost.

``logfire_api`` is a hard dependency via ``pydantic-evals``/``pydantic-graph``
(reached through ``pydantic-ai``), and it forwards to real ``logfire`` when the
extra is installed and no-ops otherwise. Importing it unconditionally -- never
``try/except ImportError`` -- covers both cases with one line.
"""

import importlib.util
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import logfire_api as logfire

from valcore.config import FileConfig, apply_logfire_token, logfire_token_present
from valcore.models import Dataset, DatasetRow, EvaluatorVersion, Run

_configured = False


class _NoOpSpan:
    """Stand-in yielded by ``run_span``/``row_span`` when tracing is unconfigured.

    Calling ``logfire.span`` before ``logfire.configure`` runs -- e.g. when the
    real ``logfire`` package is installed but a caller never opted in -- emits
    ``LogfireNotConfiguredWarning``. Yielding this instead keeps the context
    managers genuinely silent no-ops, matching what unconfigured callers expect.
    """

    def set_attribute(self, *args: Any, **kwargs: Any) -> None:
        """Discard the attribute; there is no span to attach it to."""


def configure_tracing(cfg: FileConfig) -> None:
    """Apply the configured Logfire token to the environment and configure Logfire.

    Idempotent -- safe to call from both the CLI group callback and the FastAPI
    lifespan without reconfiguring. ``console=False`` because valcore's CLI
    renders its own progress and tables, which Logfire's console exporter would
    otherwise interleave with.

    Warns exactly once when a token is configured but the real ``logfire``
    package is absent, since the shim silently discards the token in that case
    and a user who configured one expects traces. Attribute checks cannot tell
    the shim apart from the real module -- it masquerades as it -- so presence
    is detected with ``importlib.util.find_spec``.

    The idempotency flag is set only after every step below succeeds. If
    ``logfire.configure`` (or an earlier step) raises, the module must not be
    left permanently marked configured -- that would strand it in a state
    where spans quietly stay unconfigured forever with no way to retry.
    """
    global _configured
    if _configured:
        return

    apply_logfire_token(cfg)

    if logfire_token_present(cfg) and importlib.util.find_spec("logfire") is None:
        warnings.warn(
            "A Logfire token is configured but the 'logfire' extra is not installed; "
            "traces will not be sent. Install it with: uv tool install 'valcore[logfire]'",
            UserWarning,
            stacklevel=2,
        )

    logfire.configure(
        send_to_logfire="if-token-present",
        service_name="valcore",
        console=False,
    )

    # After configure, never before: instrumenting an uninitialized instance emits
    # logfire's own "not initialized for instrumentation" warning. No argument means
    # every agent -- the evaluator, the generator, the refiner, and datagen -- rather
    # than only the ones a call site remembers to pass. Needs no logfire extra: the
    # integration imports from ``pydantic_ai``, already a hard dependency, so unlike
    # ``instrument_fastapi`` this cannot fail on a missing package.
    logfire.instrument_pydantic_ai()

    _configured = True


def instrument_app(app: Any) -> None:
    """Instrument a FastAPI app, but only when Logfire's FastAPI extra is installed.

    Unlike ``configure`` and ``span``, ``instrument_fastapi`` does **not** degrade to a
    no-op: it raises ``RuntimeError`` when ``opentelemetry-instrumentation-fastapi`` is
    missing. And the shim offers no protection here, because ``pydantic-ai-slim`` depends
    on ``logfire[httpx]`` -- so real ``logfire`` is present in every install and
    ``logfire_api`` forwards to it, while the FastAPI instrumentation ships only in
    valcore's own ``[logfire]`` extra. Guarding on ``logfire`` would therefore still
    crash; the guard has to be on the instrumentation package itself.
    """
    if importlib.util.find_spec("opentelemetry.instrumentation.fastapi") is None:
        return
    logfire.instrument_fastapi(app)


@contextmanager
def run_span(
    run: Run, version: EvaluatorVersion, dataset: Dataset, row_count: int
) -> Iterator[Any]:
    """Open the ``valcore.run`` span that parents every ``valcore.score_row`` span.

    Yields the span so the caller can set ``status`` and each metrics key as
    attributes before it closes -- metrics as attributes rather than a log line
    so a Logfire query can filter runs by accuracy without a join.

    A no-op when ``configure_tracing`` has never successfully run, so callers
    need no conditionals and unconfigured processes never touch ``logfire.span``.
    """
    if not _configured:
        yield _NoOpSpan()
        return
    with logfire.span(
        "valcore.run",
        run_id=run.id,
        kind=run.kind.value,
        version_id=version.id,
        version_name=version.version_name,
        dataset_id=dataset.id,
        dataset_name=dataset.name,
        row_count=row_count,
        concurrency=run.concurrency,
    ) as span:
        yield span


@contextmanager
def row_span(row: DatasetRow) -> Iterator[Any]:
    """Open the ``valcore.score_row`` span for a single row.

    The Gateway's own LLM span attaches beneath this automatically via the
    injected ``traceparent``; this module emits nothing for the LLM call itself.

    A no-op when ``configure_tracing`` has never successfully run, so callers
    need no conditionals and unconfigured processes never touch ``logfire.span``.
    """
    if not _configured:
        yield _NoOpSpan()
        return
    with logfire.span("valcore.score_row", row_id=row.id, idx=row.idx) as span:
        yield span
