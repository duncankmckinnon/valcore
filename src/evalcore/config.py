"""TOML config layer stored at ``~/.eval-core/config.toml``.

Read with the stdlib :mod:`tomllib`; written by hand (five keys does not justify
a TOML-writing dependency). ``apply_gateway_key`` is the single bridge between the
stored config and pydantic-ai: nothing else in the codebase reads, stores, or
passes ``PYDANTIC_AI_GATEWAY_API_KEY``.
"""

import os
import tempfile
import tomllib
import warnings
from pathlib import Path

from pydantic import BaseModel

from evalcore.paths import config_path

_GATEWAY_KEY_ENV = "PYDANTIC_AI_GATEWAY_API_KEY"


class FileConfig(BaseModel):
    """Values persisted in ``config.toml``. All optional; missing keys are ``None``."""

    gateway_api_key: str | None = None
    model: str | None = None
    port: int | None = None
    concurrency: int | None = None
    db_path: Path | None = None


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
