"""FastAPI application factory: CORS, exception handlers, health/config, routers, static SPA."""

import importlib
from importlib.resources import files as _package_files
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from evalcore.api.dtos import ErrorBody, ErrorResponse
from evalcore.errors import (
    ConfigError,
    ContractError,
    EvalCoreError,
    FrozenVersionError,
    NotFoundError,
)
from evalcore.models import VALID_CAPABILITIES
from evalcore.settings import MODEL_CATALOG
from evalcore.tools import tool_names

_STATUS_BY_ERROR: tuple[tuple[type[EvalCoreError], int], ...] = (
    (NotFoundError, 404),
    (ContractError, 422),
    (ConfigError, 422),
    (FrozenVersionError, 409),
    (EvalCoreError, 400),
)


def _resolve_dist_dir() -> Path | None:
    """Locate the built SPA: packaged assets first, then a repo checkout, else nothing."""
    try:
        packaged = _package_files("evalcore") / "web_dist"
        if packaged.is_dir():
            return Path(str(packaged))
    except (ModuleNotFoundError, FileNotFoundError):
        pass

    for parent in Path(__file__).resolve().parents:
        candidate = parent / "web" / "dist"
        if candidate.is_dir():
            return candidate

    return None


def _error_response(status_code: int, exc: Exception) -> JSONResponse:
    """Render an exception into the uniform ``{"error": {type, message}}`` envelope."""
    body = ErrorResponse(error=ErrorBody(type=type(exc).__name__, message=str(exc)))
    return JSONResponse(status_code=status_code, content=body.model_dump())


def _register_exception_handlers(app: FastAPI) -> None:
    """Map each domain error to its documented HTTP status via the uniform envelope."""

    def make_handler(status_code: int):
        async def handler(_request: Request, exc: EvalCoreError) -> JSONResponse:
            return _error_response(status_code, exc)

        return handler

    for error_type, status_code in _STATUS_BY_ERROR:
        app.add_exception_handler(error_type, make_handler(status_code))


def _include_routers(app: FastAPI) -> None:
    """Discover and mount resource routers, tolerating ones that do not exist yet."""
    for module_name in ("evaluators", "datasets", "runs"):
        try:
            module = importlib.import_module(f"evalcore.api.routes.{module_name}")
        except ImportError:
            continue
        app.include_router(module.router)


def create_app() -> FastAPI:
    """Build and return the eval-core FastAPI application."""
    app = FastAPI(title="eval-core")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_exception_handlers(app)

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
        app.mount("/", StaticFiles(directory=dist_dir, html=True), name="spa")

    return app
