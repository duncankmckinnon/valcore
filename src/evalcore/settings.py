"""Environment config, gateway model resolution, and the model catalog.

This module is the single place model strings are validated. Every other module
imports from here and never constructs or parses model strings itself.
"""

import functools
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from evalcore.errors import ConfigError

GATEWAY_ROUTES: tuple[str, ...] = (
    "gateway/anthropic",
    "gateway/openai",
    "gateway/google",
    "gateway/google-cloud",
    "gateway/bedrock",
    "gateway/groq",
)

MODEL_CATALOG: list[str] = [
    "gateway/anthropic:claude-sonnet-5",
    "gateway/anthropic:claude-opus-4-5",
    "gateway/anthropic:claude-haiku-4-5",
    "gateway/openai:gpt-5",
    "gateway/google:gemini-2.5-pro",
]


class Settings(BaseSettings):
    """Runtime configuration read from the environment (prefix ``EVALCORE_``)."""

    model_config = SettingsConfigDict(env_prefix="EVALCORE_")

    db_path: Path = Path("evalcore.db")
    default_model: str = "gateway/anthropic:claude-sonnet-5"
    default_concurrency: int = 8
    logfire_enabled: bool = False


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
