"""Keeps README.md's command table from silently drifting away from reference.md's.

reference.md (shipped as part of the use-valcore skill) is the source of truth for
which CLI commands exist and what each one does; README.md's own summary table is
generated from it. This test runs the generator in check mode so a hand-edit to
either file without updating the other fails CI instead of quietly drifting.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_readme_command_table_matches_reference_md() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sync_command_table.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
