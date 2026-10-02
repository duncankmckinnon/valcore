"""FastAPI dependencies shared across resource routers."""

import functools
from typing import Annotated

from fastapi import Depends

from valcore import config
from valcore.agent_prompt_sync import AgentPromptSync
from valcore.logfire_prompt_variables import PromptVariableAdapter
from valcore.settings import get_settings, is_local_cli_model
from valcore.store import Store, create_engine, init_db


@functools.lru_cache
def get_store() -> Store:
    """Return the process-wide Store, creating the engine and tables on first use.

    Cached so a single engine is shared across requests. Tests override this via
    ``app.dependency_overrides[get_store]``.
    """
    engine = create_engine(get_settings().db_path)
    init_db(engine)
    return Store(engine)


def require_gateway_key_unless_local() -> None:
    """Require the gateway key unless generation resolves to a local CLI model.

    Generation routes pass no explicit model, so they resolve to
    ``get_settings().default_model``. A ``local/<cli>`` default reaches an
    already-logged-in CLI on this machine, never the gateway, so demanding a gateway
    key there would block the exact keyless setup local CLI models exist for.
    """
    if not is_local_cli_model(get_settings().default_model):
        config.require_gateway_key()


def get_prompt_sync(store: Annotated[Store, Depends(get_store)]) -> AgentPromptSync:
    """Build the request's explicit prompt-sync service with the configured key."""
    adapter = PromptVariableAdapter()
    return AgentPromptSync(store, adapter, adapter.key_fingerprint)
