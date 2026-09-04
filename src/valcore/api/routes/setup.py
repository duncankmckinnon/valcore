"""Setup status and key writes.

GET reports which configuration keys are effectively set. POST writes values to the
local config file. No key value ever crosses a response -- only booleans, computed
from the same ``*_present`` helpers ``require_gateway_key`` relies on, so presence
reflects an exported env var exactly as it does everywhere else in the codebase.
"""

import os
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from valcore import logfire_links
from valcore.config import (
    clear_gateway_key,
    clear_logfire_read_key,
    clear_logfire_token,
    clear_logfire_write_key,
    gateway_key_present,
    load_config,
    logfire_read_key_present,
    logfire_token_present,
    logfire_write_key_present,
    migrate_legacy_logfire_api_key,
    resolve_logfire_read_key,
    save_config,
    set_key,
    set_logfire_read_key,
    set_logfire_token,
    set_logfire_write_key,
)
from valcore.errors import ContractError

router = APIRouter(prefix="/api/setup", tags=["setup"])

_CLEARABLE = ("gateway_api_key", "logfire_token", "logfire_read_key", "logfire_write_key")
ClearName = Literal["gateway_api_key", "logfire_token", "logfire_read_key", "logfire_write_key"]


class KeyStatus(BaseModel):
    """Presence and static metadata for one configuration key. Never carries its value."""

    name: str
    set: bool
    required: bool
    label: str
    command: str
    purpose: str
    explanation: str
    from_env: bool


class SetupOut(BaseModel):
    """The full setup status: one entry per documented configuration key.

    ``logfire_explore_url``, ``logfire_traces_url``, and ``logfire_datasets_url`` are not
    secrets. They default to pages derived from the read key's project; a stored Explore
    URL is only used when lookup fails.
    """

    keys: list[KeyStatus]
    logfire_explore_url: str | None = None
    logfire_traces_url: str | None = None
    logfire_datasets_url: str | None = None


class SetupKeysIn(BaseModel):
    """Values to persist. Omitted fields stay unchanged; ``clear`` unsets named keys."""

    gateway_api_key: str | None = None
    logfire_token: str | None = None
    logfire_read_key: str | None = None
    logfire_write_key: str | None = None
    clear: list[ClearName] = Field(default_factory=list)


_GATEWAY_EXPLANATION = (
    "valcore reaches models only through the Pydantic AI Gateway — there is no "
    "direct-to-provider client. Without this key, generation and runs cannot call a "
    "model. Authoring by hand, uploading a CSV, labeling, and export still work. "
    "Create the key in the Pydantic AI Gateway. This is not a Logfire credential."
)
_TOKEN_EXPLANATION = (
    "A project write token for the Logfire project that should receive valcore's own "
    "telemetry. With it configured, FastAPI requests, pydantic-ai agent activity, and "
    "each valcore.run / valcore.score_row span are sent there. It cannot query traces "
    "or push datasets — those use the read and write keys, which may target a "
    "different project. Create it from that project's settings."
)
_READ_EXPLANATION = (
    "An API key for the Logfire project you operate on — which may be separate from "
    "the project that receives valcore's traces. It runs SQL queries and pulls traces "
    "into a local dataset. It needs the project:read scope. If the same key also has "
    "project:write_datasets, you can use it as the write key too."
)
_WRITE_EXPLANATION = (
    "An API key for pushing datasets to Logfire's hosted store in the project you "
    "operate on. It needs the project:read_datasets and project:write_datasets scopes. "
    "Paste the same value as the read key when one API key carries query, read, and "
    "dataset-write permissions."
)


async def _status() -> SetupOut:
    """Build the current setup payload from the on-disk config and environment."""
    cfg = load_config()
    derived = await logfire_links.resolve_logfire_links(resolve_logfire_read_key(cfg))
    return SetupOut(
        keys=[
            KeyStatus(
                name="gateway_api_key",
                set=gateway_key_present(cfg),
                required=True,
                label="Pydantic AI Gateway key",
                command="valcore config set-key",
                purpose="Runs evaluators and generates evaluators and datasets.",
                explanation=_GATEWAY_EXPLANATION,
                from_env="PYDANTIC_AI_GATEWAY_API_KEY" in os.environ,
            ),
            KeyStatus(
                name="logfire_token",
                set=logfire_token_present(cfg),
                required=False,
                label="Logfire tracing token",
                command="valcore config set-logfire-token",
                purpose=(
                    "Sends valcore's FastAPI, gateway, and run traces to your valcore "
                    "Logfire project."
                ),
                explanation=_TOKEN_EXPLANATION,
                from_env="LOGFIRE_TOKEN" in os.environ,
            ),
            KeyStatus(
                name="logfire_read_key",
                set=logfire_read_key_present(cfg),
                required=False,
                label="Logfire read key",
                command="valcore config set-logfire-read-key",
                purpose="Queries traces in the Logfire project you are sampling from.",
                explanation=_READ_EXPLANATION,
                from_env=False,
            ),
            KeyStatus(
                name="logfire_write_key",
                set=logfire_write_key_present(cfg),
                required=False,
                label="Logfire write key",
                command="valcore config set-logfire-write-key",
                purpose="Pushes datasets to the Logfire project you operate on.",
                explanation=_WRITE_EXPLANATION,
                from_env=False,
            ),
        ],
        logfire_explore_url=(derived.explore_url if derived else None) or cfg.logfire_explore_url,
        logfire_traces_url=derived.traces_url if derived else None,
        logfire_datasets_url=derived.datasets_url if derived else None,
    )


@router.get("", response_model=SetupOut)
async def get_setup() -> SetupOut:
    """Report effective presence for the gateway key and the three Logfire credentials."""
    return await _status()


def _require_nonblank(name: str, value: str | None) -> str | None:
    """Treat omitted as unchanged; reject whitespace-only as a client error."""
    if value is None:
        return None
    stripped = value.strip()
    if stripped == "":
        raise ContractError(f"{name} must not be blank.")
    return stripped


@router.post("", response_model=SetupOut)
async def post_setup(body: SetupKeysIn) -> SetupOut:
    """Persist the provided keys to the local config file and return updated presence."""
    values = {
        "gateway_api_key": _require_nonblank("gateway_api_key", body.gateway_api_key),
        "logfire_token": _require_nonblank("logfire_token", body.logfire_token),
        "logfire_read_key": _require_nonblank("logfire_read_key", body.logfire_read_key),
        "logfire_write_key": _require_nonblank("logfire_write_key", body.logfire_write_key),
    }
    overlapping = [name for name in body.clear if values[name] is not None]
    if overlapping:
        raise ContractError(f"Cannot set and clear {overlapping[0]} in the same request.")
    unknown = [name for name in body.clear if name not in _CLEARABLE]
    if unknown:
        raise ContractError(f"Unknown key to clear: {unknown[0]}.")

    touching_split = (
        values["logfire_read_key"] is not None
        or values["logfire_write_key"] is not None
        or "logfire_read_key" in body.clear
        or "logfire_write_key" in body.clear
    )
    if touching_split:
        cfg = migrate_legacy_logfire_api_key(load_config())
        save_config(cfg)

    if values["gateway_api_key"] is not None:
        set_key(values["gateway_api_key"])
    if values["logfire_token"] is not None:
        set_logfire_token(values["logfire_token"])
    if values["logfire_read_key"] is not None:
        set_logfire_read_key(values["logfire_read_key"])
    if values["logfire_write_key"] is not None:
        set_logfire_write_key(values["logfire_write_key"])

    for name in body.clear:
        if name == "gateway_api_key":
            clear_gateway_key()
        elif name == "logfire_token":
            clear_logfire_token()
        elif name == "logfire_read_key":
            clear_logfire_read_key()
        elif name == "logfire_write_key":
            clear_logfire_write_key()

    return await _status()
