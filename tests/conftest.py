from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point VALCORE_HOME at a tmp dir so no test touches a real home directory.

    Also clears the cached settings so each test resolves against its own home.
    """
    from valcore import settings

    monkeypatch.setenv("VALCORE_HOME", str(tmp_path / "valcore-home"))
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    monkeypatch.delenv("PYDANTIC_AI_GATEWAY_API_KEY", raising=False)
    settings.get_settings.cache_clear()
    yield
    settings.get_settings.cache_clear()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def make_engine() -> Iterator[Callable[..., Engine]]:
    """Create engines that are disposed when the test ends.

    An engine left undisposed keeps its SQLite connection alive until the garbage
    collector finalises it. Python raises a ResourceWarning at that point, charged to
    whichever unrelated test happens to be running -- and pytest-cov re-enables that
    warning for the whole session, so it can fail an assertion about warnings far away
    from the test that actually leaked.
    """
    from valcore.store import create_engine

    engines: list[Engine] = []

    def make(db_path: Path | None = None) -> Engine:
        engine = create_engine(db_path)
        engines.append(engine)
        return engine

    try:
        yield make
    finally:
        for engine in engines:
            engine.dispose()
