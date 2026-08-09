"""Read-only setup status: which configuration keys are effectively set.

Keys are written only via the CLI (``valcore config set-key`` and its Logfire siblings), so
there is no POST here and no key value ever crosses this endpoint -- only booleans, computed
from the same ``*_present`` helpers ``require_gateway_key`` relies on, so presence reflects an
exported env var exactly as it does everywhere else in the codebase.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from valcore.config import (
    gateway_key_present,
    load_config,
    logfire_api_key_present,
    logfire_token_present,
)

router = APIRouter(prefix="/api/setup", tags=["setup"])


class KeyStatus(BaseModel):
    """Presence and static metadata for one configuration key. Never carries its value."""

    name: str
    set: bool
    required: bool
    label: str
    command: str
    purpose: str


class SetupOut(BaseModel):
    """The full setup status: one entry per documented configuration key."""

    keys: list[KeyStatus]


@router.get("", response_model=SetupOut)
async def get_setup() -> SetupOut:
    """Report effective presence for the gateway key and both Logfire credentials."""
    cfg = load_config()
    return SetupOut(
        keys=[
            KeyStatus(
                name="gateway_api_key",
                set=gateway_key_present(cfg),
                required=True,
                label="Pydantic AI Gateway key",
                command="valcore config set-key",
                purpose="Runs evaluators and generates evaluators and datasets.",
            ),
            KeyStatus(
                name="logfire_token",
                set=logfire_token_present(cfg),
                required=False,
                label="Logfire write token",
                command="valcore config set-logfire-token",
                purpose="Sends run traces to Logfire.",
            ),
            KeyStatus(
                name="logfire_api_key",
                set=logfire_api_key_present(cfg),
                required=False,
                label="Logfire API key",
                command="valcore config set-logfire-key",
                purpose="Pushes datasets to Logfire's hosted store.",
            ),
        ]
    )
