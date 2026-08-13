"""Environment config, gateway model resolution, and the model catalog.

This module is the single place model strings are validated. Every other module
imports from here and never constructs or parses model strings itself.
"""

import functools
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from valcore.config import load_config
from valcore.errors import ConfigError
from valcore.paths import default_db_path

GATEWAY_ROUTES: tuple[str, ...] = (
    "gateway/anthropic",
    "gateway/openai",
    "gateway/google",
    "gateway/google-cloud",
    "gateway/bedrock",
    "gateway/groq",
)


@functools.lru_cache
def model_catalog() -> tuple[str, ...]:
    """Return the gateway-routable model names known to the pinned ``pydantic-ai``.

    These are type-ahead suggestions, not a whitelist -- ``validate_model_string``
    accepts any well-formed ``gateway/<route>:<name>`` string and leaves it to the
    Gateway to reject names it does not serve. Filtering through ``GATEWAY_ROUTES``
    (rather than a bare ``gateway/`` prefix) keeps the two in step: every suggestion
    is a string ``validate_model_string`` accepts.

    ``pydantic_ai.models`` is imported here rather than at module scope because it
    costs ~235ms to import, and every CLI invocation loads this module while only the
    API's config endpoint needs the catalog.
    """
    from pydantic_ai.models import known_model_names

    prefixes = tuple(f"{route}:" for route in GATEWAY_ROUTES)
    return tuple(sorted(n for n in known_model_names() if n.startswith(prefixes)))


class _TomlConfigSource(PydanticBaseSettingsSource):
    """Settings source backed by ``config.toml``, sitting below the env source.

    Maps the config file's keys onto ``Settings`` fields: ``model`` ->
    ``default_model``, ``concurrency`` -> ``default_concurrency``, and ``db_path``
    passes through unchanged.
    """

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        cfg = load_config()
        mapped: dict[str, Any] = {}
        if cfg.db_path is not None:
            mapped["db_path"] = cfg.db_path
        if cfg.model is not None:
            mapped["default_model"] = cfg.model
        if cfg.concurrency is not None:
            mapped["default_concurrency"] = cfg.concurrency
        self._values = mapped

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self._values.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(self._values)


class Settings(BaseSettings):
    """Runtime configuration.

    Resolution order (highest precedence first): explicit argument, ``VALCORE_*``
    environment variable, ``config.toml``, then the built-in default.
    """

    model_config = SettingsConfigDict(env_prefix="VALCORE_")

    db_path: Path = Field(default_factory=default_db_path)
    default_model: str = "gateway/anthropic:claude-sonnet-5"
    default_concurrency: int = 8
    logfire_enabled: bool = False

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _TomlConfigSource(settings_cls),
            file_secret_settings,
        )


@functools.lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()


def validate_model_string(model: str) -> None:
    """Raise ConfigError unless ``model`` is a valid ``gateway/<provider>:<name>`` string."""
    for route in GATEWAY_ROUTES:
        prefix = f"{route}:"
        if model.startswith(prefix):
            name = model[len(prefix) :]
            if not name:
                raise ConfigError(
                    f"Model string {model!r} is missing a model name after {route!r}."
                )
            return
    raise ConfigError(
        f"Model string {model!r} must start with one of {GATEWAY_ROUTES} followed by ':<model>'."
    )
