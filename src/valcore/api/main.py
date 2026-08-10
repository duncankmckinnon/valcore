"""FastAPI application factory: CORS, exception handlers, health/config, routers, static SPA."""

import importlib
from importlib.resources import files as _package_files
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Scope

from valcore.api.dtos import ErrorBody, ErrorResponse
from valcore.config import load_config
from valcore.errors import (
    ConfigError,
    ContractError,
    DestructiveChangeError,
    FrozenVersionError,
    NotFoundError,
    ReferencedError,
    ValcoreError,
)
from valcore.models import VALID_CAPABILITIES
from valcore.settings import MODEL_CATALOG
from valcore.tools import tool_names
from valcore.tracing import configure_tracing, instrument_app

_STATUS_BY_ERROR: tuple[tuple[type[ValcoreError], int], ...] = (
    (NotFoundError, 404),
    (ContractError, 422),
    (ConfigError, 422),
    (FrozenVersionError, 409),
    (ReferencedError, 409),
    (DestructiveChangeError, 409),
    (ValcoreError, 400),
)


def _resolve_dist_dir() -> Path | None:
    """Locate the built SPA: packaged assets first, then a repo checkout, else nothing."""
    try:
        packaged = _package_files("valcore") / "web_dist"
        if packaged.is_dir():
            return Path(str(packaged))
    except (ModuleNotFoundError, FileNotFoundError):
        pass

    for parent in Path(__file__).resolve().parents:
        candidate = parent / "web" / "dist"
        if candidate.is_dir():
            return candidate

    return None


class _Spa(StaticFiles):
    """Serve the built SPA with history routing and a revalidated shell.

    Two behaviours ``StaticFiles`` does not provide on its own:

    **History-routing fallback.** ``html=True`` only serves ``index.html`` for *directory*
    requests and looks for a ``404.html`` on a miss, so a client-side route such as
    ``/evaluators`` 404s on a deep link, a refresh, or any full navigation. Clicking
    through from ``/`` hides this because react-router never reaches the server. Unknown
    non-API paths therefore fall back to the shell and let the client router resolve them.

    **Cache correctness.** Without ``Cache-Control`` a browser applies heuristic freshness
    and serves a cached ``index.html`` without revalidating; that shell references the
    previous build's hashed assets, which are cached too, so a rebuilt UI never appears.
    The shell is marked ``no-cache`` so it always revalidates -- cheap, since the ``ETag``
    turns that into a 304 -- while content-hashed assets are cached immutably, which is
    what makes the hashes worth having.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        """Return the asset at ``path``, or the shell for an unresolved client route.

        A miss surfaces as a raised ``HTTPException(404)`` rather than a 404 response --
        ``StaticFiles`` only returns one when a ``404.html`` exists -- so the fallback has
        to catch, not inspect a status code.
        """
        try:
            response = await super().get_response(path, scope)
            served_shell = False
        except HTTPException as exc:
            # A miss under /api is a genuine 404: answering it with the SPA shell would
            # hand an API client an HTML body instead of an error it can read. Anything
            # else is treated as a client-side route and resolved by the browser router.
            if exc.status_code != 404 or path == "api" or path.startswith("api/"):
                raise
            response = await super().get_response("index.html", scope)
            served_shell = True

        is_shell = served_shell or path in ("", ".", "index.html")
        response.headers["cache-control"] = (
            "no-cache" if is_shell else "public, max-age=31536000, immutable"
        )
        return response


def _error_response(status_code: int, exc: Exception) -> JSONResponse:
    """Render an exception into the uniform ``{"error": {type, message}}`` envelope."""
    body = ErrorResponse(
        error=ErrorBody(
            type=type(exc).__name__,
            message=str(exc),
            detail=getattr(exc, "detail", None) or None,
        )
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(exclude_none=True))


def _register_exception_handlers(app: FastAPI) -> None:
    """Map each domain error to its documented HTTP status via the uniform envelope."""

    def make_handler(status_code: int):
        async def handler(_request: Request, exc: ValcoreError) -> JSONResponse:
            return _error_response(status_code, exc)

        return handler

    for error_type, status_code in _STATUS_BY_ERROR:
        app.add_exception_handler(error_type, make_handler(status_code))


def _include_routers(app: FastAPI) -> None:
    """Discover and mount resource routers, tolerating ones that do not exist yet."""
    for module_name in ("evaluators", "datasets", "runs", "overview", "setup"):
        try:
            module = importlib.import_module(f"valcore.api.routes.{module_name}")
        except ImportError:
            continue
        app.include_router(module.router)


def create_app() -> FastAPI:
    """Build and return the valcore FastAPI application."""
    app = FastAPI(title="valcore")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_exception_handlers(app)

    configure_tracing(load_config())
    instrument_app(app)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    @app.get("/api/config")
    async def config() -> dict[str, list[str]]:
        """Return the pickers the SPA needs: models, tools, and capabilities."""
        return {
            "models": list(MODEL_CATALOG),
            "tools": tool_names(),
            "capabilities": sorted(VALID_CAPABILITIES),
        }

    _include_routers(app)

    dist_dir = _resolve_dist_dir()
    if dist_dir:
        app.mount("/", _Spa(directory=dist_dir, html=True), name="spa")

    return app
