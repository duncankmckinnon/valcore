"""Logfire UI URLs for the project an API key is scoped to.

A project-scoped token (or API key used as one) identifies a single organization and
project via ``GET /v1/read-token-info``. Combined with the region encoded in the token, that
is enough to open SQL Workbench, the live traces view, the datasets list, and one dataset's
cases page. Callers pass the read key for Workbench/traces and the write key for hosted
datasets. Lookup failures are silent: callers treat ``None`` as "no link".
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from httpx import Timeout

_TRACES_QUERY = "last=%2230m%22"


@dataclass(frozen=True)
class LogfireLinks:
    """The Logfire pages valcore links to for one reference project."""

    explore_url: str
    traces_url: str
    datasets_url: str


def links_for(base_url: str, organization: str, project: str) -> LogfireLinks:
    """Build Workbench, traces, and datasets URLs for ``organization`` / ``project``."""
    root = f"{base_url.rstrip('/')}/{quote(organization, safe='')}/{quote(project, safe='-_.')}"
    return LogfireLinks(
        explore_url=f"{root}/explore",
        traces_url=f"{root}?{_TRACES_QUERY}",
        datasets_url=f"{root}/evals",
    )


def dataset_cases_url(datasets_url: str, name: str) -> str:
    """Return the cases page for a hosted dataset named ``name`` under ``datasets_url``."""
    return f"{datasets_url.rstrip('/')}/{quote(name, safe='-_.')}/cases"


async def resolve_logfire_links(
    api_key: str | None,
    *,
    timeout: float = 5.0,
) -> LogfireLinks | None:
    """Return project links for ``api_key``, or ``None`` if they cannot be resolved.

    Uses the query client's ``info()`` (``GET /v1/read-token-info``) and the region baked into
    the token. Any missing key, import error, or request failure yields ``None`` so a down
    Logfire never takes the setup endpoint with it.
    """
    if api_key is None:
        return None
    try:
        from logfire._internal.config import get_base_url_from_token
        from logfire.query_client import AsyncLogfireQueryClient
    except ImportError:
        return None
    try:
        async with AsyncLogfireQueryClient(read_token=api_key, timeout=Timeout(timeout)) as client:
            info = await client.info()
        organization = info.get("organization_name")
        project = info.get("project_name")
        if not organization or not project:
            return None
        return links_for(get_base_url_from_token(api_key), organization, project)
    except Exception:  # noqa: BLE001 — lookup must never take the setup endpoint down
        return None
