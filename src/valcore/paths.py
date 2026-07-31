"""Filesystem locations for valcore state, all rooted at ``~/.valcore``.

``VALCORE_HOME`` overrides the root so tests and CI never touch a real home
directory. Directories are created with mode ``0700``; an existing directory is
left as-is and its permissions are never widened.
"""

import os
from pathlib import Path


def _ensure_dir(path: Path) -> Path:
    """Create ``path`` with mode ``0700`` if missing; leave an existing dir alone."""
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
    return path


def home_dir() -> Path:
    """Return the valcore home directory, creating it ``0700`` on first call.

    Uses ``$VALCORE_HOME`` when set, otherwise ``~/.valcore``.
    """
    env = os.environ.get("VALCORE_HOME")
    root = Path(env) if env else Path.home() / ".valcore"
    return _ensure_dir(root)


def config_path() -> Path:
    """Return the path to ``config.toml`` under the home directory."""
    return home_dir() / "config.toml"


def default_db_path() -> Path:
    """Return the default SQLite database path under the home directory."""
    return home_dir() / "valcore.db"


def logs_dir() -> Path:
    """Return the logs directory under the home directory, creating it on demand."""
    return _ensure_dir(home_dir() / "logs")
