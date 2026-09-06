"""Tests for the home directory, TOML config layer, and settings precedence."""

import importlib.util
import os
import stat
import warnings
from pathlib import Path

import pytest

from valcore import settings
from valcore.config import (
    FileConfig,
    apply_gateway_key,
    apply_logfire_token,
    clear_gateway_key,
    clear_logfire_read_key,
    clear_logfire_token,
    clear_logfire_write_key,
    gateway_key_present,
    load_config,
    logfire_api_key_present,
    logfire_read_key_present,
    logfire_token_present,
    logfire_write_key_present,
    require_gateway_key,
    resolve_logfire_read_key,
    resolve_logfire_write_key,
    save_config,
    set_key,
    set_logfire_api_key,
    set_logfire_explore_url,
    set_logfire_read_key,
    set_logfire_token,
    set_logfire_write_key,
)
from valcore.errors import ConfigError
from valcore.paths import config_path, default_db_path, home_dir


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point VALCORE_HOME at a tmp dir and clear the settings cache."""
    root = tmp_path / "home"
    monkeypatch.setenv("VALCORE_HOME", str(root))
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
    assert cfg.logfire_token is None
    assert cfg.logfire_api_key is None
    assert cfg.logfire_read_key is None
    assert cfg.logfire_write_key is None
    assert cfg.logfire_explore_url is None


def test_save_load_round_trips_every_field() -> None:
    cfg = FileConfig(
        gateway_api_key="sk-secret",
        model="gateway/openai:gpt-5",
        port=9123,
        concurrency=4,
        db_path=Path("/tmp/custom.db"),
        logfire_token="lf-write-token",
        logfire_api_key="lf-api-key",
        logfire_read_key="lf-read-key",
        logfire_write_key="lf-write-key",
        logfire_explore_url="https://logfire-us.pydantic.dev/duncan/agent-tracing/explore",
    )
    save_config(cfg)
    loaded = load_config()
    assert loaded == cfg


def test_save_load_round_trips_logfire_fields_only() -> None:
    """A config with only the logfire fields set dumps only those lines."""
    cfg = FileConfig(logfire_token="lf-write-token", logfire_api_key="lf-api-key")
    save_config(cfg)

    content = config_path().read_text()
    assert 'logfire_token = "lf-write-token"' in content
    assert 'logfire_api_key = "lf-api-key"' in content
    # No other fields were set, so no other lines should appear.
    assert "gateway_api_key" not in content
    assert "model" not in content
    assert "port" not in content
    assert "concurrency" not in content
    assert "db_path" not in content

    loaded = load_config()
    assert loaded == cfg


def test_dump_toml_omits_unset_logfire_fields() -> None:
    """The dumper writes only non-None fields; logfire fields are no exception."""
    save_config(FileConfig(gateway_api_key="sk-secret"))
    content = config_path().read_text()
    assert "logfire_token" not in content
    assert "logfire_api_key" not in content
    assert "logfire_read_key" not in content
    assert "logfire_write_key" not in content
    assert "logfire_explore_url" not in content


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


def test_apply_logfire_token_sets_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    cfg = FileConfig(logfire_token="lf-from-config")

    assert apply_logfire_token(cfg) is True
    assert os.environ["LOGFIRE_TOKEN"] == "lf-from-config"


def test_apply_logfire_token_respects_existing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOGFIRE_TOKEN", "lf-from-env")
    cfg = FileConfig(logfire_token="lf-from-config")

    assert apply_logfire_token(cfg) is False
    assert os.environ["LOGFIRE_TOKEN"] == "lf-from-env"


def test_apply_logfire_token_returns_false_with_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    cfg = FileConfig()

    assert apply_logfire_token(cfg) is False
    assert "LOGFIRE_TOKEN" not in os.environ


def test_set_key_preserves_other_fields() -> None:
    save_config(FileConfig(model="gateway/openai:gpt-5", concurrency=3))
    set_key("sk-added")
    loaded = load_config()
    assert loaded.gateway_api_key == "sk-added"
    assert loaded.model == "gateway/openai:gpt-5"
    assert loaded.concurrency == 3


def test_set_logfire_token_preserves_other_fields() -> None:
    save_config(
        FileConfig(
            gateway_api_key="sk-existing",
            model="gateway/openai:gpt-5",
            concurrency=3,
            logfire_api_key="lf-existing-api-key",
        )
    )
    set_logfire_token("lf-added-token")
    loaded = load_config()
    assert loaded.logfire_token == "lf-added-token"
    assert loaded.gateway_api_key == "sk-existing"
    assert loaded.model == "gateway/openai:gpt-5"
    assert loaded.concurrency == 3
    assert loaded.logfire_api_key == "lf-existing-api-key"


def test_set_logfire_api_key_preserves_other_fields() -> None:
    save_config(
        FileConfig(
            gateway_api_key="sk-existing",
            model="gateway/openai:gpt-5",
            concurrency=3,
            logfire_token="lf-existing-token",
        )
    )
    set_logfire_api_key("lf-added-api-key")
    loaded = load_config()
    assert loaded.logfire_read_key == "lf-added-api-key"
    assert loaded.logfire_write_key == "lf-added-api-key"
    assert loaded.logfire_api_key is None
    assert loaded.gateway_api_key == "sk-existing"
    assert loaded.model == "gateway/openai:gpt-5"
    assert loaded.concurrency == 3
    assert loaded.logfire_token == "lf-existing-token"


def test_set_logfire_explore_url_preserves_other_fields() -> None:
    save_config(FileConfig(logfire_api_key="lf-existing-api-key"))
    set_logfire_explore_url("https://logfire-us.pydantic.dev/duncan/agent-tracing/explore")
    loaded = load_config()
    assert (
        loaded.logfire_explore_url == "https://logfire-us.pydantic.dev/duncan/agent-tracing/explore"
    )
    assert loaded.logfire_api_key == "lf-existing-api-key"


def test_db_path_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    # default: no env, no config -> default_db_path()
    settings.get_settings.cache_clear()
    assert settings.Settings().db_path == default_db_path()

    # TOML beats default.
    save_config(FileConfig(db_path=Path("/tmp/from-toml.db")))
    settings.get_settings.cache_clear()
    assert settings.Settings().db_path == Path("/tmp/from-toml.db")

    # env beats TOML.
    monkeypatch.setenv("VALCORE_DB_PATH", "/tmp/from-env.db")
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
    monkeypatch.setenv("VALCORE_DEFAULT_MODEL", "gateway/google:gemini-2.5-pro")
    monkeypatch.setenv("VALCORE_DEFAULT_CONCURRENCY", "16")
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


# --- Effective-presence helpers -------------------------------------------------
#
# A key is "present" when either its env var is exported or the file config
# carries it. gateway_key_present and logfire_token_present each have an env
# source, so four cases apiece; logfire_api_key_present is file-only, so three.


def test_gateway_key_present_false_from_neither(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYDANTIC_AI_GATEWAY_API_KEY", raising=False)
    assert gateway_key_present(FileConfig()) is False


def test_gateway_key_present_true_from_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_API_KEY", "sk-from-env")
    assert gateway_key_present(FileConfig()) is True


def test_gateway_key_present_true_from_file_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYDANTIC_AI_GATEWAY_API_KEY", raising=False)
    assert gateway_key_present(FileConfig(gateway_api_key="sk-from-file")) is True


def test_gateway_key_present_true_from_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_API_KEY", "sk-from-env")
    assert gateway_key_present(FileConfig(gateway_api_key="sk-from-file")) is True


def test_logfire_token_present_false_from_neither(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    assert logfire_token_present(FileConfig()) is False


def test_logfire_token_present_true_from_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOGFIRE_TOKEN", "lf-from-env")
    assert logfire_token_present(FileConfig()) is True


def test_logfire_token_present_true_from_file_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    assert logfire_token_present(FileConfig(logfire_token="lf-from-file")) is True


def test_logfire_token_present_true_from_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOGFIRE_TOKEN", "lf-from-env")
    assert logfire_token_present(FileConfig(logfire_token="lf-from-file")) is True


def test_logfire_api_key_present_false_with_none() -> None:
    assert logfire_api_key_present(FileConfig()) is False


def test_logfire_api_key_present_true_from_file() -> None:
    assert logfire_api_key_present(FileConfig(logfire_api_key="lf-api-key")) is True


def test_logfire_api_key_present_ignores_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API key has no env var; even a same-named env var must not count."""
    monkeypatch.setenv("LOGFIRE_API_KEY", "lf-from-env")
    assert logfire_api_key_present(FileConfig()) is False


def test_save_load_round_trips_read_and_write_keys() -> None:
    cfg = FileConfig(logfire_read_key="lf-read", logfire_write_key="lf-write")
    save_config(cfg)
    content = config_path().read_text()
    assert 'logfire_read_key = "lf-read"' in content
    assert 'logfire_write_key = "lf-write"' in content
    assert load_config() == cfg


def test_set_logfire_read_key_preserves_other_fields() -> None:
    save_config(
        FileConfig(
            gateway_api_key="sk-existing",
            logfire_token="lf-existing-token",
            logfire_write_key="lf-existing-write",
        )
    )
    set_logfire_read_key("lf-added-read")
    loaded = load_config()
    assert loaded.logfire_read_key == "lf-added-read"
    assert loaded.gateway_api_key == "sk-existing"
    assert loaded.logfire_token == "lf-existing-token"
    assert loaded.logfire_write_key == "lf-existing-write"


def test_set_logfire_write_key_preserves_other_fields() -> None:
    save_config(
        FileConfig(
            gateway_api_key="sk-existing",
            logfire_token="lf-existing-token",
            logfire_read_key="lf-existing-read",
        )
    )
    set_logfire_write_key("lf-added-write")
    loaded = load_config()
    assert loaded.logfire_write_key == "lf-added-write"
    assert loaded.gateway_api_key == "sk-existing"
    assert loaded.logfire_token == "lf-existing-token"
    assert loaded.logfire_read_key == "lf-existing-read"


def test_resolve_logfire_read_key_prefers_read_then_legacy_and_ignores_write() -> None:
    """The write key targets the valcore project; it must not stand in for query/pull."""
    assert resolve_logfire_read_key(FileConfig()) is None
    assert resolve_logfire_read_key(FileConfig(logfire_write_key="lf-write")) is None
    assert (
        resolve_logfire_read_key(
            FileConfig(logfire_api_key="lf-legacy", logfire_write_key="lf-write")
        )
        == "lf-legacy"
    )
    assert (
        resolve_logfire_read_key(
            FileConfig(
                logfire_read_key="lf-read",
                logfire_api_key="lf-legacy",
                logfire_write_key="lf-write",
            )
        )
        == "lf-read"
    )


def test_resolve_logfire_write_key_prefers_write_then_legacy_and_ignores_read() -> None:
    """The read key targets the source-trace project; it must not stand in for dataset push."""
    assert resolve_logfire_write_key(FileConfig()) is None
    assert resolve_logfire_write_key(FileConfig(logfire_read_key="lf-read")) is None
    assert (
        resolve_logfire_write_key(
            FileConfig(logfire_api_key="lf-legacy", logfire_read_key="lf-read")
        )
        == "lf-legacy"
    )
    assert (
        resolve_logfire_write_key(
            FileConfig(
                logfire_write_key="lf-write",
                logfire_api_key="lf-legacy",
                logfire_read_key="lf-read",
            )
        )
        == "lf-write"
    )


def test_logfire_read_key_present_from_own_field_or_legacy() -> None:
    assert logfire_read_key_present(FileConfig()) is False
    assert logfire_read_key_present(FileConfig(logfire_read_key="lf-read")) is True
    assert logfire_read_key_present(FileConfig(logfire_api_key="lf-legacy")) is True
    assert logfire_read_key_present(FileConfig(logfire_write_key="lf-write")) is False


def test_logfire_write_key_present_from_own_field_or_legacy() -> None:
    assert logfire_write_key_present(FileConfig()) is False
    assert logfire_write_key_present(FileConfig(logfire_write_key="lf-write")) is True
    assert logfire_write_key_present(FileConfig(logfire_api_key="lf-legacy")) is True
    assert logfire_write_key_present(FileConfig(logfire_read_key="lf-read")) is False


def test_clear_helpers_unset_only_the_named_field() -> None:
    save_config(
        FileConfig(
            gateway_api_key="sk-secret",
            logfire_token="lf-token",
            logfire_read_key="lf-read",
            logfire_write_key="lf-write",
        )
    )
    clear_gateway_key()
    loaded = load_config()
    assert loaded.gateway_api_key is None
    assert loaded.logfire_token == "lf-token"
    assert loaded.logfire_read_key == "lf-read"
    assert loaded.logfire_write_key == "lf-write"

    clear_logfire_token()
    loaded = load_config()
    assert loaded.logfire_token is None
    assert loaded.logfire_read_key == "lf-read"

    clear_logfire_read_key()
    loaded = load_config()
    assert loaded.logfire_read_key is None
    assert loaded.logfire_write_key == "lf-write"

    clear_logfire_write_key()
    assert load_config().logfire_write_key is None


# --- require_gateway_key ---------------------------------------------------------


def test_require_gateway_key_passes_with_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_API_KEY", "sk-from-env")
    require_gateway_key()  # must not raise


def test_require_gateway_key_passes_with_file_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYDANTIC_AI_GATEWAY_API_KEY", raising=False)
    save_config(FileConfig(gateway_api_key="sk-from-file"))
    require_gateway_key()  # must not raise


def test_require_gateway_key_raises_with_neither(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYDANTIC_AI_GATEWAY_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="valcore config set-key"):
        require_gateway_key()


# --- dev-group extra guard ---------------------------------------------------------


def test_logfire_extra_is_present_in_dev_environment() -> None:
    """Guards the pyproject.toml dev-group entry.

    Without this, a dropped `logfire` dev-group line would silently skip the
    span tests in test_tracing.py and test_runner.py forever instead of
    failing loudly here.
    """
    assert importlib.util.find_spec("logfire") is not None


# --- model catalog -----------------------------------------------------------------


def test_model_catalog_is_derived_and_covers_every_gateway_route() -> None:
    """The catalog comes from pydantic-ai, not a hand-maintained literal.

    The old five-entry list went stale silently. Asserting a floor well above it,
    plus coverage of every route, means a future pydantic-ai bump that drops a
    provider fails here instead of quietly shrinking the picker.
    """
    catalog = settings.model_catalog()

    assert len(catalog) > 100
    routes_seen = {name.split(":", 1)[0] for name in catalog}
    assert routes_seen == set(settings.GATEWAY_ROUTES)


def test_every_catalog_entry_passes_model_validation() -> None:
    """The catalog and the validator cannot drift apart.

    `model_catalog` filters through GATEWAY_ROUTES precisely so that suggesting a
    model the validator would then reject is impossible.
    """
    for name in settings.model_catalog():
        settings.validate_model_string(name)


def test_default_model_is_in_the_catalog() -> None:
    assert settings.Settings().default_model in settings.model_catalog()


def test_validate_model_string_accepts_names_absent_from_the_catalog() -> None:
    """Shape is the only gate; the Gateway is the authority on what exists.

    The Gateway serves more models than any pinned pydantic-ai knows about, so an
    unrecognised-but-well-formed name must pass rather than block the user.
    """
    unknown = "gateway/anthropic:some-model-released-tomorrow"
    assert unknown not in settings.model_catalog()
    settings.validate_model_string(unknown)  # must not raise


def test_validate_model_string_still_rejects_malformed_names() -> None:
    for bad in ("claude-sonnet-5", "gateway/nope:x", "gateway/anthropic:"):
        with pytest.raises(ConfigError):
            settings.validate_model_string(bad)


def test_validate_model_string_accepts_local_cli_routes() -> None:
    for name in ("local/claude:sonnet", "local/codex:gpt-5-codex", "local/cursor:composer"):
        settings.validate_model_string(name)  # must not raise


def test_validate_model_string_rejects_malformed_local_cli_names() -> None:
    for bad in ("local/claude", "local/claude:", "local/unknown-cli:sonnet"):
        with pytest.raises(ConfigError):
            settings.validate_model_string(bad)


def test_is_local_cli_model_distinguishes_routes() -> None:
    assert settings.is_local_cli_model("local/claude:sonnet") is True
    assert settings.is_local_cli_model("gateway/anthropic:claude-sonnet-5") is False


def test_model_catalog_still_only_covers_gateway_routes() -> None:
    """Local routes are validated but not suggested -- there is no fixed name list for them."""
    catalog = settings.model_catalog()
    routes_seen = {name.split(":", 1)[0] for name in catalog}
    assert routes_seen == set(settings.GATEWAY_ROUTES)
