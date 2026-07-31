"""FastAPI dependencies shared across resource routers."""

import functools

from evalcore.settings import get_settings
from evalcore.store import Store, create_engine, init_db


@functools.lru_cache
def get_store() -> Store:
    """Return the process-wide Store, creating the engine and tables on first use.

    Cached so a single engine is shared across requests. Tests override this via
    ``app.dependency_overrides[get_store]``.
    """
    engine = create_engine(get_settings().db_path)
    init_db(engine)
    return Store(engine)
