from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point EVALCORE_HOME at a tmp dir so no test touches a real home directory.

    Also clears the cached settings so each test resolves against its own home.
    """
    from evalcore import settings

    monkeypatch.setenv("EVALCORE_HOME", str(tmp_path / "eval-core-home"))
    settings.get_settings.cache_clear()
    yield
    settings.get_settings.cache_clear()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
