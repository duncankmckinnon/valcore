"""Tests for SPA asset resolution and mounting in the packaged wheel and repo checkout."""

from pathlib import Path

import httpx
import pytest

from valcore.api import main
from valcore.api.main import _resolve_dist_dir, create_app


def _client(app) -> httpx.AsyncClient:
    """Return an ASGI-backed client bound to the given app."""
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _patch_resources(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Make the packaged-assets lookup inside main resolve to ``root``."""
    monkeypatch.setattr(main, "_package_files", lambda _package: root)


def test_resolver_prefers_packaged_assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    packaged = tmp_path / "pkg"
    (packaged / "web_dist").mkdir(parents=True)
    _patch_resources(monkeypatch, packaged)

    assert _resolve_dist_dir() == packaged / "web_dist"


def test_resolver_falls_back_to_repo_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Packaged assets absent: point the resource lookup at an empty dir.
    _patch_resources(monkeypatch, tmp_path / "empty")

    # A repo checkout with web/dist sitting above the module file.
    repo = tmp_path / "checkout"
    dist = repo / "web" / "dist"
    dist.mkdir(parents=True)
    monkeypatch.setattr(main, "__file__", str(repo / "src" / "valcore" / "api" / "main.py"))

    assert _resolve_dist_dir() == dist


def test_resolver_returns_none_when_nothing_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_resources(monkeypatch, tmp_path / "empty")
    monkeypatch.setattr(main, "__file__", str(tmp_path / "nowhere" / "main.py"))

    assert _resolve_dist_dir() is None


@pytest.mark.anyio
async def test_app_starts_and_serves_health_without_spa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_resources(monkeypatch, tmp_path / "empty")
    monkeypatch.setattr(main, "__file__", str(tmp_path / "nowhere" / "main.py"))

    app = create_app()
    async with _client(app) as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_app_mounts_spa_when_index_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packaged = tmp_path / "pkg"
    web_dist = packaged / "web_dist"
    web_dist.mkdir(parents=True)
    (web_dist / "index.html").write_text("<!doctype html><title>valcore</title>")
    _patch_resources(monkeypatch, packaged)

    app = create_app()
    async with _client(app) as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert "valcore" in response.text
