"""TOML config layer stored at ``~/.valcore/config.toml``.

Read with the stdlib :mod:`tomllib`; written by hand (seven keys does not justify
a TOML-writing dependency). ``apply_gateway_key`` and ``apply_logfire_token`` are
the only bridges between the stored config and the environment variables that
pydantic-ai and logfire read; nothing else in the codebase reads, stores, or
passes ``PYDANTIC_AI_GATEWAY_API_KEY`` or ``LOGFIRE_TOKEN``. The Logfire API key
has no env var and is never exported; it is read directly from ``FileConfig`` by
whatever calls the datasets API.
"""

import os
import tempfile
import tomllib
import warnings
from pathlib import Path

from pydantic import BaseModel

from valcore.errors import ConfigError
from valcore.paths import config_path

_GATEWAY_KEY_ENV = "PYDANTIC_AI_GATEWAY_API_KEY"
_LOGFIRE_TOKEN_ENV = "LOGFIRE_TOKEN"


class FileConfig(BaseModel):
    """Values persisted in ``config.toml``. All optional; missing keys are ``None``."""

    gateway_api_key: str | None = None
    model: str | None = None
    port: int | None = None
    concurrency: int | None = None
    db_path: Path | None = None
    logfire_token: str | None = None
    logfire_api_key: str | None = None


def _toml_str(value: str) -> str:
    """Quote ``value`` as a TOML basic string, escaping backslashes and quotes."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _dump_toml(cfg: FileConfig) -> str:
    """Serialise the set (non-``None``) fields of ``cfg`` to TOML text."""
    lines: list[str] = []
    if cfg.gateway_api_key is not None:
        lines.append(f"gateway_api_key = {_toml_str(cfg.gateway_api_key)}")
    if cfg.model is not None:
        lines.append(f"model = {_toml_str(cfg.model)}")
    if cfg.port is not None:
        lines.append(f"port = {cfg.port}")
    if cfg.concurrency is not None:
        lines.append(f"concurrency = {cfg.concurrency}")
    if cfg.db_path is not None:
        lines.append(f"db_path = {_toml_str(str(cfg.db_path))}")
    if cfg.logfire_token is not None:
        lines.append(f"logfire_token = {_toml_str(cfg.logfire_token)}")
    if cfg.logfire_api_key is not None:
        lines.append(f"logfire_api_key = {_toml_str(cfg.logfire_api_key)}")
    return "\n".join(lines) + ("\n" if lines else "")


def load_config() -> FileConfig:
    """Load ``config.toml``, returning an all-``None`` config when it is missing.

    Never raises for a missing file. Emits a :class:`UserWarning` naming the path
    when the file is group- or world-readable, but still loads it.
    """
    path = config_path()
    if not path.exists():
        return FileConfig()
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        warnings.warn(
            f"Config file {path} is group- or world-readable (mode {mode:03o}); "
            f"run 'chmod 600 {path}' to restrict it.",
            UserWarning,
            stacklevel=2,
        )
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return FileConfig.model_validate(data)


def save_config(cfg: FileConfig) -> None:
    """Write ``cfg`` to ``config.toml`` atomically with mode ``0600``.

    Writes a temp file in the same directory, ``chmod 0600``, then ``os.replace``
    so an interrupted write never leaves a truncated config or a loosely
    permissioned key.
    """
    path = config_path()
    content = _dump_toml(cfg)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".toml")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
        tmp.chmod(0o600)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def set_key(key: str) -> None:
    """Persist ``key`` as the gateway API key, preserving other config values."""
    cfg = load_config()
    cfg.gateway_api_key = key
    save_config(cfg)


def apply_gateway_key(cfg: FileConfig) -> bool:
    """Export the stored gateway key to the environment when it is not already set.

    Returns ``True`` if the environment variable was set from ``cfg``. An
    explicitly exported ``PYDANTIC_AI_GATEWAY_API_KEY`` always wins.
    """
    if cfg.gateway_api_key is None:
        return False
    if _GATEWAY_KEY_ENV in os.environ:
        return False
    os.environ[_GATEWAY_KEY_ENV] = cfg.gateway_api_key
    return True


def set_logfire_token(token: str) -> None:
    """Persist ``token`` as the Logfire write token, preserving other config values."""
    cfg = load_config()
    cfg.logfire_token = token
    save_config(cfg)


def set_logfire_api_key(key: str) -> None:
    """Persist ``key`` as the Logfire management API key, preserving other config values."""
    cfg = load_config()
    cfg.logfire_api_key = key
    save_config(cfg)


def apply_logfire_token(cfg: FileConfig) -> bool:
    """Export the stored Logfire token to the environment when it is not already set.

    Returns ``True`` if the environment variable was set from ``cfg``. An
    explicitly exported ``LOGFIRE_TOKEN`` always wins.
    """
    if cfg.logfire_token is None:
        return False
    if _LOGFIRE_TOKEN_ENV in os.environ:
        return False
    os.environ[_LOGFIRE_TOKEN_ENV] = cfg.logfire_token
    return True


def gateway_key_present(cfg: FileConfig) -> bool:
    """Report whether the gateway key is effectively present, from env or ``cfg``."""
    return _GATEWAY_KEY_ENV in os.environ or cfg.gateway_api_key is not None


def logfire_token_present(cfg: FileConfig) -> bool:
    """Report whether the Logfire token is effectively present, from env or ``cfg``."""
    return _LOGFIRE_TOKEN_ENV in os.environ or cfg.logfire_token is not None


def logfire_api_key_present(cfg: FileConfig) -> bool:
    """Report whether the Logfire API key is present. File-only; there is no env var."""
    return cfg.logfire_api_key is not None


def require_gateway_key() -> None:
    """Raise :class:`ConfigError` unless the gateway key is effectively present.

    A missing gateway key otherwise fails deep inside request handling: the
    provider raises a bare ``UserError`` that becomes a 500, and because
    ``build_agent`` defers the check, a run instead records one failure per row.
    """
    if not gateway_key_present(load_config()):
        raise ConfigError(
            "No gateway API key configured. Run 'valcore config set-key' or export "
            f"{_GATEWAY_KEY_ENV}."
        )
