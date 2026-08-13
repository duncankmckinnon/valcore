"""Tests for the Homebrew launcher ``packaging/valcore.sh``.

The launcher provisions a wheel-only venv under ``$VALCORE_HOME`` on first run and
re-provisions when the version stamp is stale (the ``brew upgrade`` path). These tests
exercise it with a **stub ``uv``** on ``PATH`` -- never the real one -- and
``VALCORE_HOME`` pointed at ``tmp_path``, so nothing touches a real home or the network.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parent.parent / "packaging" / "valcore.sh"

# A fake ``uv`` that logs every invocation and materializes the files the launcher
# expects: ``uv venv`` creates the venv bin dir and interpreter, ``uv pip install`` drops
# in an executable ``valcore`` entrypoint that echoes a marker so we can prove the exec
# passthrough happened.
#
# The ``venv`` case refuses a path that already holds a venv unless ``--clear`` is passed,
# because that is what the real uv does. The original stub used a bare ``mkdir -p``, which
# succeeded either way -- so the upgrade path looked healthy in tests while failing for
# real users the moment no TTY was attached to answer uv's replace prompt.
#
# ``UV_FAIL_INSTALL_MARKER``, when it points at an existing file, makes ``pip install``
# fail; ``venv --clear`` removes it. That models a reused venv whose interpreter no longer
# satisfies the new release's requires-python, and recovering by rebuilding.
FAKE_UV = """#!/bin/bash
echo "$@" >> "$UV_LOG"
case "$1" in
  venv)
    venv="${@: -1}"
    if [ -d "$venv" ] && [[ "$*" != *--clear* ]]; then
      echo "error: A virtual environment already exists at: $venv" >&2
      exit 1
    fi
    rm -f "${UV_FAIL_INSTALL_MARKER:-/nonexistent}"
    rm -rf "$venv"
    mkdir -p "$venv/bin"
    printf '#!/bin/bash\\n' > "$venv/bin/python"
    chmod +x "$venv/bin/python"
    ;;
  pip)
    python="$4"
    bin="$(dirname "$python")"
    if [ -f "${UV_FAIL_INSTALL_MARKER:-/nonexistent}" ]; then
      echo "error: no interpreter satisfies requires-python" >&2
      exit 1
    fi
    mkdir -p "$bin"
    printf '#!/bin/bash\\necho "VALCORE_RAN $@"\\n' > "$bin/valcore"
    chmod +x "$bin/valcore"
    ;;
esac
"""


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """A launcher environment with a stub ``uv`` first on ``PATH``."""
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    uv = stub_bin / "uv"
    uv.write_text(FAKE_UV)
    uv.chmod(0o755)

    home = tmp_path / "valcore-home"
    uv_log = tmp_path / "uv.log"

    return {
        "PATH": f"{stub_bin}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path / "fake-home"),
        "VALCORE_HOME": str(home),
        "VALCORE_VERSION": "0.1.0",
        "UV_LOG": str(uv_log),
    }


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(LAUNCHER), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _log(env: dict[str, str]) -> str:
    log = Path(env["UV_LOG"])
    return log.read_text() if log.exists() else ""


def _called_venv(log: str) -> bool:
    """Whether `uv venv` was invoked, ignoring the venv path in `pip install` lines."""
    return any(line.startswith("venv ") for line in log.splitlines())


def test_first_run_provisions_venv(env: dict[str, str]) -> None:
    result = _run(env, "serve")
    assert result.returncode == 0, result.stderr

    home = Path(env["VALCORE_HOME"])
    assert home.is_dir()
    assert oct(home.stat().st_mode & 0o777) == "0o700"

    log = _log(env)
    assert "venv --clear --python >=3.11" in log
    # --refresh-package valcore: the pinned version is published moments before the
    # formula bump, so uv's cached index listing for valcore predates it and resolution
    # fails with "there is no version of valcore==X". Scoped to the one package so the
    # cache still serves the dependency tree.
    assert (
        f"pip install --python {home}/venv/bin/python "
        "--refresh-package valcore valcore==0.1.0" in log
    )

    stamp = home / "venv" / ".version"
    assert stamp.read_text() == "0.1.0"


def test_second_run_skips_provisioning(env: dict[str, str]) -> None:
    first = _run(env, "serve")
    assert first.returncode == 0, first.stderr

    # Wipe the log so any second-run uv call would show up.
    Path(env["UV_LOG"]).write_text("")

    second = _run(env, "run", "--json")
    assert second.returncode == 0, second.stderr

    assert _log(env) == ""  # uv called zero times
    assert "VALCORE_RAN run --json" in second.stdout


def test_stale_stamp_triggers_reprovision(env: dict[str, str]) -> None:
    """A `brew upgrade` reinstalls the pinned version without recreating the venv.

    The venv is reused deliberately. Recreating it would be slow (~115 packages for a
    version bump) and, worse, `uv venv` refuses a path that already holds a venv: it
    prompts at a TTY and fails outright without one, so under `set -e` every post-upgrade
    run from a script or CI job used to die before reaching the install.
    """
    first = _run(env, "serve")
    assert first.returncode == 0, first.stderr

    # Simulate an older install: rewrite the stamp to a prior version.
    stamp = Path(env["VALCORE_HOME"]) / "venv" / ".version"
    stamp.write_text("0.0.9")
    Path(env["UV_LOG"]).write_text("")

    second = _run(env, "serve")
    assert second.returncode == 0, second.stderr

    log = _log(env)
    # Match the *command*, not the substring: every `pip install` line contains the venv
    # path, so a bare `"venv" not in log` would never hold.
    assert not _called_venv(log), "upgrade must reuse the existing venv, not recreate it"
    assert "pip install" in log
    assert stamp.read_text() == "0.1.0"
    assert "VALCORE_RAN serve" in second.stdout


def test_upgrade_reports_upgrading_not_first_run(env: dict[str, str]) -> None:
    _run(env, "serve")
    (Path(env["VALCORE_HOME"]) / "venv" / ".version").write_text("0.0.9")

    result = _run(env, "serve")

    assert "upgrading environment to 0.1.0" in result.stderr
    assert "first run" not in result.stderr


def test_upgrade_rebuilds_when_the_reused_venv_cannot_take_the_install(
    env: dict[str, str], tmp_path: Path
) -> None:
    """A venv whose interpreter no longer satisfies requires-python is rebuilt, not fatal."""
    _run(env, "serve")

    stamp = Path(env["VALCORE_HOME"]) / "venv" / ".version"
    stamp.write_text("0.0.9")

    # Poison installs into the existing venv; only `venv --clear` clears the marker.
    marker = tmp_path / "install-fails"
    marker.write_text("")
    env = {**env, "UV_FAIL_INSTALL_MARKER": str(marker)}
    Path(env["UV_LOG"]).write_text("")

    result = _run(env, "serve")
    assert result.returncode == 0, result.stderr

    log = _log(env)
    assert "venv --clear" in log, "must rebuild after the in-place install fails"
    assert "rebuilding" in result.stderr
    assert stamp.read_text() == "0.1.0"
    assert "VALCORE_RAN serve" in result.stdout


def test_partial_venv_from_an_interrupted_run_is_cleared(env: dict[str, str]) -> None:
    """A venv dir with no interpreter is a half-written first run, not something to reuse."""
    venv = Path(env["VALCORE_HOME"]) / "venv"
    venv.mkdir(parents=True)
    (venv / "lib").mkdir()

    result = _run(env, "serve")
    assert result.returncode == 0, result.stderr

    assert "first run" in result.stderr
    assert "venv --clear" in _log(env)
    assert "VALCORE_RAN serve" in result.stdout


def test_missing_version_exits_nonzero(env: dict[str, str]) -> None:
    del env["VALCORE_VERSION"]
    result = _run(env, "serve")
    assert result.returncode != 0
    assert _log(env) == ""  # never got as far as calling uv


def test_provisioning_output_goes_to_stderr(env: dict[str, str]) -> None:
    result = _run(env, "run", "--json")
    assert result.returncode == 0, result.stderr

    assert "provisioning" in result.stderr
    # stdout carries only the exec'd program's output, so ``| jq`` stays clean.
    assert "provisioning" not in result.stdout
    assert result.stdout.strip() == "VALCORE_RAN run --json"


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not on PATH")
def test_launcher_passes_shellcheck() -> None:
    result = subprocess.run(
        ["shellcheck", str(LAUNCHER)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
