"""Project links derived from a Logfire read token: SQL Workbench, traces, and datasets."""

import sys
import types
from dataclasses import dataclass
from typing import Self

import pytest

from valcore.logfire_links import dataset_cases_url, links_for, resolve_logfire_links


def test_links_for_builds_explore_traces_and_evals() -> None:
    links = links_for("https://logfire-us.pydantic.dev", "duncan", "agent-tracing")
    assert links.explore_url == "https://logfire-us.pydantic.dev/duncan/agent-tracing/explore"
    assert links.traces_url == (
        "https://logfire-us.pydantic.dev/duncan/agent-tracing?last=%2230m%22"
    )
    assert links.datasets_url == "https://logfire-us.pydantic.dev/duncan/agent-tracing/evals"


def test_dataset_cases_url_appends_the_dataset_name() -> None:
    assert (
        dataset_cases_url(
            "https://logfire-us.pydantic.dev/duncan/agent-tracing/evals",
            "agent_responses",
        )
        == "https://logfire-us.pydantic.dev/duncan/agent-tracing/evals/agent_responses/cases"
    )


def test_links_for_strips_a_trailing_slash_on_the_base() -> None:
    links = links_for("https://logfire-us.pydantic.dev/", "duncan", "agent-tracing")
    assert links.datasets_url == "https://logfire-us.pydantic.dev/duncan/agent-tracing/evals"


@dataclass
class _InfoRecorder:
    organization: str = "duncan"
    project: str = "agent-tracing"
    constructed_with: str | None = None
    error: Exception | None = None


def _install_stub_query_client(monkeypatch: pytest.MonkeyPatch, recorder: _InfoRecorder) -> None:
    class StubAsyncClient:
        def __init__(self, read_token: str, **_kwargs: object) -> None:
            recorder.constructed_with = read_token

        async def __aenter__(self) -> Self:
            if recorder.error is not None:
                raise recorder.error
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
            return False

        async def info(self) -> dict[str, str]:
            return {
                "organization_name": recorder.organization,
                "project_name": recorder.project,
            }

    fake = types.ModuleType("logfire.query_client")
    fake.AsyncLogfireQueryClient = StubAsyncClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "logfire.query_client", fake)


@pytest.mark.anyio
async def test_resolve_logfire_links_returns_none_without_a_key() -> None:
    assert await resolve_logfire_links(None) is None


@pytest.mark.anyio
async def test_resolve_logfire_links_builds_urls_from_read_token_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _InfoRecorder()
    _install_stub_query_client(monkeypatch, recorder)
    links = await resolve_logfire_links("lf-read")
    assert recorder.constructed_with == "lf-read"
    assert links is not None
    assert links.explore_url == "https://logfire-us.pydantic.dev/duncan/agent-tracing/explore"
    assert links.traces_url == (
        "https://logfire-us.pydantic.dev/duncan/agent-tracing?last=%2230m%22"
    )
    assert links.datasets_url == "https://logfire-us.pydantic.dev/duncan/agent-tracing/evals"


@pytest.mark.anyio
async def test_resolve_logfire_links_returns_none_when_info_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _InfoRecorder(error=RuntimeError("not authorized"))
    _install_stub_query_client(monkeypatch, recorder)
    assert await resolve_logfire_links("lf-read") is None
