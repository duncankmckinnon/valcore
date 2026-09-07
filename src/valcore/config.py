"""TOML config layer stored at ``~/.valcore/config.toml``.

Read with the stdlib :mod:`tomllib`; written by hand (ten keys does not justify
a TOML-writing dependency). ``apply_gateway_key`` and ``apply_logfire_token`` are
the only bridges between the stored config and the environment variables that
pydantic-ai and logfire read; nothing else in the codebase reads, stores, or
passes ``PYDANTIC_AI_GATEWAY_API_KEY`` or ``LOGFIRE_TOKEN``. Logfire read/write
API keys have no env var and are never exported; they are read directly from
``FileConfig`` by whatever calls the datasets API. A legacy ``logfire_api_key``
still loads and fills both until the split fields are set.
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
    local_cli_default: str | None = None
    port: int | None = None
    concurrency: int | None = None
    db_path: Path | None = None
    logfire_token: str | None = None
    logfire_api_key: str | None = None
    logfire_read_key: str | None = None
    logfire_write_key: str | None = None
    logfire_explore_url: str | None = None


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
    if cfg.local_cli_default is not None:
        lines.append(f"local_cli_default = {_toml_str(cfg.local_cli_default)}")
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
    if cfg.logfire_read_key is not None:
        lines.append(f"logfire_read_key = {_toml_str(cfg.logfire_read_key)}")
    if cfg.logfire_write_key is not None:
        lines.append(f"logfire_write_key = {_toml_str(cfg.logfire_write_key)}")
    if cfg.logfire_explore_url is not None:
        lines.append(f"logfire_explore_url = {_toml_str(cfg.logfire_explore_url)}")
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


def sync_logfire_token_env(cfg: FileConfig) -> None:
    """Force the environment to match ``cfg``'s Logfire token, overwriting or clearing it.

    Unlike ``apply_logfire_token``, an existing ``LOGFIRE_TOKEN`` does not win here --
    this exists for the moment a running process's token changes through valcore's own
    config (e.g. a settings-UI save hitting ``POST /api/setup``), and the value valcore
    itself exported at startup needs to move to match so ``tracing.reconfigure_logfire_token``
    can pick it up without a restart.
    """
    if cfg.logfire_token is None:
        os.environ.pop(_LOGFIRE_TOKEN_ENV, None)
    else:
        os.environ[_LOGFIRE_TOKEN_ENV] = cfg.logfire_token


def set_logfire_token(token: str) -> None:
    """Persist ``token`` as the Logfire write token, preserving other config values."""
    cfg = load_config()
    cfg.logfire_token = token
    save_config(cfg)


def set_logfire_api_key(key: str) -> None:
    """Persist ``key`` as both the Logfire read and write keys.

    This is the combined-key path: one API key with query, read, and dataset-write
    scopes. Older configs may still carry ``logfire_api_key``; new writes go to the
    split fields so Settings can show each one independently.
    """
    cfg = load_config()
    cfg.logfire_read_key = key
    cfg.logfire_write_key = key
    cfg.logfire_api_key = None
    save_config(cfg)


def set_logfire_read_key(key: str) -> None:
    """Persist the Logfire read key used to query traces and hosted datasets in the source project."""
    cfg = load_config()
    cfg.logfire_read_key = key
    save_config(cfg)


def set_logfire_write_key(key: str) -> None:
    """Persist the Logfire write key used to push datasets to the valcore project."""
    cfg = load_config()
    cfg.logfire_write_key = key
    save_config(cfg)


def clear_gateway_key() -> None:
    """Remove the stored gateway API key, preserving other config values."""
    cfg = load_config()
    cfg.gateway_api_key = None
    save_config(cfg)


def set_local_cli_default(name: str) -> None:
    """Persist `name` ('claude'/'codex'/'cursor') as the default local CLI model.

    Not validated here: `config.py` has no import of `valcore.settings` (which would cycle
    back through `settings.py -> config.py`). The caller validates `name` against
    `valcore.settings.LOCAL_CLI_NAMES` before calling this.
    """
    cfg = load_config()
    cfg.local_cli_default = name
    save_config(cfg)


def clear_local_cli_default() -> None:
    """Remove the stored local-CLI default, preserving other config values."""
    cfg = load_config()
    cfg.local_cli_default = None
    save_config(cfg)


def clear_logfire_token() -> None:
    """Remove the stored Logfire tracing token, preserving other config values."""
    cfg = load_config()
    cfg.logfire_token = None
    save_config(cfg)


def clear_logfire_read_key() -> None:
    """Remove the stored Logfire read key, preserving other config values."""
    cfg = load_config()
    cfg.logfire_read_key = None
    save_config(cfg)


def clear_logfire_write_key() -> None:
    """Remove the stored Logfire write key, preserving other config values."""
    cfg = load_config()
    cfg.logfire_write_key = None
    save_config(cfg)


def set_logfire_explore_url(url: str) -> None:
    """Persist the Logfire SQL Workbench URL, preserving other config values."""
    cfg = load_config()
    cfg.logfire_explore_url = url
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
    """Report whether a combined/legacy Logfire API key can serve read and write.

    True when the legacy field is set, or when both split fields are set. Callers
    that only need to know "can we talk to Logfire's API at all" still use this.
    """
    if cfg.logfire_api_key is not None:
        return True
    return cfg.logfire_read_key is not None or cfg.logfire_write_key is not None


def logfire_read_key_present(cfg: FileConfig) -> bool:
    """Report whether a stored read key (or legacy combined key) is present."""
    return cfg.logfire_read_key is not None or cfg.logfire_api_key is not None


def logfire_write_key_present(cfg: FileConfig) -> bool:
    """Report whether a stored write key (or legacy combined key) is present."""
    return cfg.logfire_write_key is not None or cfg.logfire_api_key is not None


def resolve_logfire_read_key(cfg: FileConfig) -> str | None:
    """Key used to query traces in the source project: read, then legacy combined.

    Does not fall back to the write key: that key is for the valcore project, which
    is not where sampled traces live.
    """
    return cfg.logfire_read_key or cfg.logfire_api_key


def resolve_logfire_write_key(cfg: FileConfig) -> str | None:
    """Key used to push datasets to the valcore project: write, then legacy combined.

    Does not fall back to the read key: that key is for the source-trace project, which
    is not where hosted datasets and experiment runs live.
    """
    return cfg.logfire_write_key or cfg.logfire_api_key


def migrate_legacy_logfire_api_key(cfg: FileConfig) -> FileConfig:
    """Copy a combined legacy key into the split fields, then drop the legacy field.

    A config that only has ``logfire_api_key`` treated that value as both read and
    write. Settings now edits those independently, so the first mutation of either
    field splits the legacy value and then applies the change.
    """
    if cfg.logfire_api_key is None:
        return cfg
    if cfg.logfire_read_key is None:
        cfg.logfire_read_key = cfg.logfire_api_key
    if cfg.logfire_write_key is None:
        cfg.logfire_write_key = cfg.logfire_api_key
    cfg.logfire_api_key = None
    return cfg


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
