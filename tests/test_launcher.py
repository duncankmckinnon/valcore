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
# expects: ``uv venv`` creates the venv bin dir, ``uv pip install`` drops in an
# executable ``valcore`` entrypoint that echoes a marker so we can prove the exec
# passthrough happened.
FAKE_UV = """#!/bin/bash
echo "$@" >> "$UV_LOG"
case "$1" in
  venv)
    venv="${@: -1}"
    mkdir -p "$venv/bin"
    ;;
  pip)
    python="$4"
    bin="$(dirname "$python")"
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


def test_first_run_provisions_venv(env: dict[str, str]) -> None:
    result = _run(env, "serve")
    assert result.returncode == 0, result.stderr

    home = Path(env["VALCORE_HOME"])
    assert home.is_dir()
    assert oct(home.stat().st_mode & 0o777) == "0o700"

    log = _log(env)
    assert "venv --python >=3.11" in log
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
    first = _run(env, "serve")
    assert first.returncode == 0, first.stderr

    # Simulate an older install: rewrite the stamp to a prior version.
    stamp = Path(env["VALCORE_HOME"]) / "venv" / ".version"
    stamp.write_text("0.0.9")
    Path(env["UV_LOG"]).write_text("")

    second = _run(env, "serve")
    assert second.returncode == 0, second.stderr

    log = _log(env)
    assert "venv --python >=3.11" in log
    assert "pip install" in log
    assert stamp.read_text() == "0.1.0"


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
