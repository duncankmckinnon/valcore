"""FastAPI dependencies shared across resource routers."""

import functools
from typing import Annotated

from fastapi import Depends

from valcore.agent_prompt_sync import AgentPromptSync
from valcore.logfire_prompt_variables import PromptVariableAdapter
from valcore.settings import get_settings
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


def get_prompt_sync(store: Annotated[Store, Depends(get_store)]) -> AgentPromptSync:
    """Build the request's explicit prompt-sync service with the configured key."""
    adapter = PromptVariableAdapter()
    return AgentPromptSync(store, adapter, adapter.key_fingerprint)
