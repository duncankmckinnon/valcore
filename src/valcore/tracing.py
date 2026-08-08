"""Logfire span shaping: the only module that talks to Logfire's instrumentation surface.

The Pydantic AI Gateway already reports every LLM call server-side and injects a
W3C ``traceparent`` header into each request, so its spans nest under whatever
local span is active. valcore's job is not to re-report LLM calls -- it is to
supply the parent context (``valcore.run`` / ``valcore.score_row``) that gives the
Gateway's spans structure. See ``docs/superpowers/specs/2026-08-08-logfire-
integration-design.md`` for the full design.

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
    """
    global _configured
    if _configured:
        return
    _configured = True

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


@contextmanager
def run_span(
    run: Run, version: EvaluatorVersion, dataset: Dataset, row_count: int
) -> Iterator[Any]:
    """Open the ``valcore.run`` span that parents every ``valcore.score_row`` span.

    Yields the span so the caller can set ``status`` and each metrics key as
    attributes before it closes -- metrics as attributes rather than a log line
    so a Logfire query can filter runs by accuracy without a join.
    """
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
    """
    with logfire.span("valcore.score_row", row_id=row.id, idx=row.idx) as span:
        yield span
