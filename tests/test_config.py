"""Tests for the home directory, TOML config layer, and settings precedence."""

import os
import stat
import warnings
from pathlib import Path

import pytest

from evalcore import settings
from evalcore.config import (
    FileConfig,
    apply_gateway_key,
    load_config,
    save_config,
    set_key,
)
from evalcore.paths import config_path, default_db_path, home_dir


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point EVALCORE_HOME at a tmp dir and clear the settings cache."""
    root = tmp_path / "home"
    monkeypatch.setenv("EVALCORE_HOME", str(root))
    settings.get_settings.cache_clear()
    yield root
    settings.get_settings.cache_clear()


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_home_dir_creates_0700_and_is_idempotent(_home: Path) -> None:
    created = home_dir()
    assert created == _home
    assert created.is_dir()
    assert _mode(created) == 0o700

    # A second call must not fail or change anything.
    again = home_dir()
    assert again == created
    assert _mode(again) == 0o700


def test_load_config_missing_returns_all_none(_home: Path) -> None:
    cfg = load_config()
    assert cfg == FileConfig()
    assert cfg.gateway_api_key is None
    assert cfg.model is None
    assert cfg.port is None
    assert cfg.concurrency is None
    assert cfg.db_path is None


def test_save_load_round_trips_every_field() -> None:
    cfg = FileConfig(
        gateway_api_key="sk-secret",
        model="gateway/openai:gpt-5",
        port=9123,
        concurrency=4,
        db_path=Path("/tmp/custom.db"),
    )
    save_config(cfg)
    loaded = load_config()
    assert loaded == cfg


def test_saved_file_mode_is_0600() -> None:
    save_config(FileConfig(model="gateway/openai:gpt-5"))
    assert _mode(config_path()) == 0o600


def test_loose_permissions_warn_and_still_load() -> None:
    save_config(FileConfig(model="gateway/openai:gpt-5"))
    path = config_path()
    path.chmod(0o644)

    with pytest.warns(UserWarning, match=str(path)):
        loaded = load_config()
    assert loaded.model == "gateway/openai:gpt-5"


def test_apply_gateway_key_sets_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYDANTIC_AI_GATEWAY_API_KEY", raising=False)
    cfg = FileConfig(gateway_api_key="sk-from-config")

    assert apply_gateway_key(cfg) is True
    assert os.environ["PYDANTIC_AI_GATEWAY_API_KEY"] == "sk-from-config"


def test_apply_gateway_key_respects_existing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_API_KEY", "sk-from-env")
    cfg = FileConfig(gateway_api_key="sk-from-config")

    assert apply_gateway_key(cfg) is False
    assert os.environ["PYDANTIC_AI_GATEWAY_API_KEY"] == "sk-from-env"


def test_set_key_preserves_other_fields() -> None:
    save_config(FileConfig(model="gateway/openai:gpt-5", concurrency=3))
    set_key("sk-added")
    loaded = load_config()
    assert loaded.gateway_api_key == "sk-added"
    assert loaded.model == "gateway/openai:gpt-5"
    assert loaded.concurrency == 3


def test_db_path_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    # default: no env, no config -> default_db_path()
    settings.get_settings.cache_clear()
    assert settings.Settings().db_path == default_db_path()

    # TOML beats default.
    save_config(FileConfig(db_path=Path("/tmp/from-toml.db")))
    settings.get_settings.cache_clear()
    assert settings.Settings().db_path == Path("/tmp/from-toml.db")

    # env beats TOML.
    monkeypatch.setenv("EVALCORE_DB_PATH", "/tmp/from-env.db")
    settings.get_settings.cache_clear()
    assert settings.Settings().db_path == Path("/tmp/from-env.db")

    # explicit argument beats env.
    assert settings.Settings(db_path=Path("/tmp/from-arg.db")).db_path == Path("/tmp/from-arg.db")


def test_model_and_concurrency_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    save_config(FileConfig(model="gateway/openai:gpt-5", concurrency=2))
    settings.get_settings.cache_clear()
    resolved = settings.Settings()
    assert resolved.default_model == "gateway/openai:gpt-5"
    assert resolved.default_concurrency == 2

    # env beats TOML.
    monkeypatch.setenv("EVALCORE_DEFAULT_MODEL", "gateway/google:gemini-2.5-pro")
    monkeypatch.setenv("EVALCORE_DEFAULT_CONCURRENCY", "16")
    settings.get_settings.cache_clear()
    from_env = settings.Settings()
    assert from_env.default_model == "gateway/google:gemini-2.5-pro"
    assert from_env.default_concurrency == 16

    # explicit argument beats env.
    explicit = settings.Settings(default_model="gateway/anthropic:claude-opus-4-5")
    assert explicit.default_model == "gateway/anthropic:claude-opus-4-5"


def test_interrupted_save_leaves_previous_file_intact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_config(FileConfig(model="gateway/openai:gpt-5"))
    original = config_path().read_text()

    def _boom(src: str, dst: str) -> None:
        raise OSError("simulated interruption")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError, match="simulated interruption"):
        save_config(FileConfig(model="gateway/anthropic:claude-opus-4-5"))

    # Previous file is untouched and readable; no stray temp files remain.
    assert config_path().read_text() == original
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert load_config().model == "gateway/openai:gpt-5"
    leftovers = list(config_path().parent.glob(".config-*.toml"))
    assert leftovers == []
