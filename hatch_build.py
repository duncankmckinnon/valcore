"""Build hook that force-includes the built SPA into the wheel when it is present.

The Vite output at ``web/dist`` is gitignored and only exists after ``npm run
build``. The release workflow builds it before ``uv build`` so it ships inside the
wheel as ``valcore/web_dist``. Declaring the force-include statically in
``pyproject.toml`` would abort every build where ``web/dist`` is absent — and
developers (and editable installs via ``uv sync``) build without running npm all
the time. So inject the force-include here only when the directory exists: a wheel
built without it still succeeds, just without a bundled UI.
"""

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class SpaDistBuildHook(BuildHookInterface):
    """Ship ``web/dist`` as ``valcore/web_dist`` when the SPA build output exists."""

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        dist_dir = Path(self.root, "web", "dist")
        if dist_dir.is_dir():
            build_data.setdefault("force_include", {})[str(dist_dir)] = "src/valcore/web_dist"
