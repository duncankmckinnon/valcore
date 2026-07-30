# eval-core — CLI, `~/.eval-core`, and Homebrew distribution

**Date:** 2026-07-30
**Status:** Approved, ready for implementation planning
**Builds on:** `2026-07-26-eval-core-design.md`

## Purpose

Turn eval-core from a repo you run with `uvicorn` into a tool you install:

```bash
brew install duncankmckinnon/tap/eval-core
eval-core config set-key sk-...
eval-core serve
```

Three parts: a CLI with headless run commands usable from CI, all state relocated to
`~/.eval-core`, and a Homebrew formula.

## Constraints discovered before designing

- **114 transitive dependencies**, 11 requiring compilation: `pydantic-core`, `jiter`, `rpds-py`,
  `tiktoken`, `watchfiles`, `cryptography`, `pydantic-monty`, `pydantic-monty-runtime`,
  `httptools`, `uvloop`, `regex`. The `virtualenv_install_with_resources` pattern used by the
  `wbcli` formula would require a Rust toolchain and 114 hand-pinned `resource` blocks. Rejected.
- **`api/` is not in the wheel.** `[tool.hatch.build.targets.wheel] packages = ["src/evalcore"]`
  ships the library only.
- **No `[project.scripts]`.** Nothing installs a command.
- **`npm run build` fails** — 6 `TS18046` errors in `web/src/api/client.test.ts` (an unnarrowed
  `catch` binding). No `web/dist` is produced, so there is no SPA to serve.
- **PyPI name `eval-core` is available** (`evalcore` is taken, but that is only a distribution
  name; our import package `evalcore` is unaffected).

## Part 1 — Making the project installable

### `api/` moves to `src/evalcore/api/`

Adding `"api"` to hatch's package list would install a top-level module named `api` into
site-packages — namespace squatting that will eventually collide. Moving it inside the package
fixes the wheel and the import path together.

Mechanical consequences: every `from api.x import y` becomes `from evalcore.api.x import y`; the
four API test modules update their imports; `[tool.pytest.ini_options] pythonpath = ["."]` is
removed, since the package is importable once installed.

The router auto-discovery loop in `api/main.py` changes its import prefix to
`evalcore.api.routes.<name>`.

### The SPA ships as package data

`web/dist` is built at release time and force-included into the wheel as
`src/evalcore/web_dist/`. It stays gitignored — build output does not belong in git.

`create_app()` currently mounts `web/dist` resolved relative to the repo. It changes to resolve
package data via `importlib.resources`, falling back to the repo-relative path when running from a
checkout, so `npm run dev` workflows keep working.

This is blocked by the `tsc` failure, which is fixed as part of this work.

### Hygiene

`evalcore.db`, `evalcore.db-shm`, `evalcore.db-wal` are added to `.gitignore` — they are currently
untracked *and* unignored, which is how a live SQLite database ended up inside the built sdist.
An sdist exclude list drops `.agents/`, `.workbench/`, `docs/`, and `*.db*`.

## Part 2 — CLI

Entry point: `[project.scripts] eval-core = "evalcore.cli:main"`.

```
eval-core serve [--port 8000] [--host 127.0.0.1] [--no-browser]
eval-core run <evaluator> <dataset> [--version NAME] [--kind validation|eval]
                                    [--concurrency 8] [--json] [--watch]
                                    [--min-accuracy FLOAT]
eval-core list evaluators | datasets | runs [--json]
eval-core export <evaluator> [--version NAME] [-o out.py]
eval-core config set-key | get | path | edit
eval-core version
```

Built with `click`, matching the `wbcli` precedent.

**Resolution by name or id prefix.** Evaluators, versions, and datasets are addressable by name or
by a unique id prefix; ambiguous input is an error listing the candidates. Requiring a 32-character
hex uuid on a command line is not a workflow.

**`--json` on every read command**, and this is what makes the tool CI-usable rather than merely
scriptable: `run --json` emits a single object with run metadata, metrics, and per-row scores, and
**exits non-zero when `--min-accuracy` is set and a validation run falls below it**. That exit code
is the hook that lets eval-core gate a pipeline.

**Stream separation.** `run` writes progress to stderr and results to stdout, so redirecting stdout
yields clean JSON. Without `--watch`, the command returns when the run completes; with it, the
process stays attached and prints per-row events as they arrive.

**No new business logic.** The CLI is a shell over `store`, `runner`, and `metrics`, talking to
SQLite directly rather than over HTTP — `run` works whether or not `serve` is up.

`serve` starts uvicorn programmatically against `create_app()` and opens a browser unless
`--no-browser` is passed.

## Part 3 — `~/.eval-core`

```
~/.eval-core/              0700
  config.toml              0600  gateway key + defaults
  eval-core.db             SQLite (plus -wal, -shm)
  venv/                    provisioned by the Homebrew launcher on first run
  logs/                    serve logs
```

Location is overridable with `EVALCORE_HOME`, which exists primarily so tests and CI never touch
a real home directory. The directory is created on first use.

### Settings precedence

```
CLI flag  >  EVALCORE_* env var  >  ~/.eval-core/config.toml  >  built-in default
```

This extends the existing `Settings` (pydantic-settings, `EVALCORE_` prefix) with a TOML source
placed below env vars. `db_path` default becomes `~/.eval-core/eval-core.db`.

### The gateway key

`config.toml` holds `gateway_api_key`. At startup, if it is set and `PYDANTIC_AI_GATEWAY_API_KEY`
is absent from the environment, the CLI exports it into `os.environ` so pydantic-ai picks it up
through its normal path. Nothing else in the codebase learns about the key — the rule that the key
is never read, stored, or passed explicitly (from the original design) still holds everywhere
below the CLI boundary.

`config.toml` is written `0600`. On read, permissions are checked and a warning is printed if the
file is group- or world-readable. `config get` masks the key; only `config get --show-key` prints
it in full.

### No automatic migration

There is no meaningful installed base, so no migration runs. If `./evalcore.db` exists in the
working directory and `~/.eval-core/eval-core.db` does not, the CLI prints a one-line notice
pointing at `--db`. It never moves or copies the file — silently relocating someone's data is worse
than making them type a flag.

## Part 4 — Homebrew

### Formula (in `duncankmckinnon/homebrew-tap`)

A thin formula: `depends_on "uv"` plus an installed launcher script. It does not vendor Python
dependencies.

```ruby
class EvalCore < Formula
  desc "Develop, improve, and run agentic evaluations locally"
  homepage "https://github.com/duncankmckinnon/eval-core"
  url "https://github.com/duncankmckinnon/eval-core/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "<computed at release>"
  license "Apache-2.0"

  depends_on "uv"

  def install
    libexec.install "packaging/eval-core.sh"
    (bin/"eval-core").write_env_script libexec/"eval-core.sh",
      EVALCORE_VERSION: version.to_s
  end

  test do
    assert_match "Usage", shell_output("#{bin}/eval-core --help")
  end
end
```

### Launcher

`packaging/eval-core.sh` provisions a venv from PyPI **wheels** on first run — no compilation, so
none of the 11 native packages need a toolchain:

```sh
#!/bin/bash
set -euo pipefail
home="${EVALCORE_HOME:-$HOME/.eval-core}"
venv="$home/venv"
stamp="$venv/.version"
version="${EVALCORE_VERSION:?}"

if [ ! -x "$venv/bin/eval-core" ] || [ "$(cat "$stamp" 2>/dev/null)" != "$version" ]; then
  mkdir -p "$home" && chmod 700 "$home"
  echo "eval-core: provisioning environment (first run)…" >&2
  uv venv --python 3.12 "$venv" >&2
  uv pip install --python "$venv/bin/python" "eval-core==$version" >&2
  printf '%s' "$version" > "$stamp"
fi

exec "$venv/bin/eval-core" "$@"
```

The version stamp means `brew upgrade` re-provisions on the next invocation rather than silently
running the old code.

Accepted trade-off: first run requires network and takes roughly 15 seconds. Every subsequent
invocation `exec`s straight into the venv with no overhead.

### Release pipeline

GitHub Actions, triggered on `v*` tags:

1. `npm ci && npm run build` in `web/`
2. copy `web/dist` → `src/evalcore/web_dist/`
3. `uv build`
4. publish to PyPI via trusted publishing (no stored token)
5. compute the sha256 of the GitHub source tarball and open a PR against the tap bumping `url`
   and `sha256`

Step 5 opens a PR rather than pushing, so a bad release never auto-publishes a formula.

## Testing

| Area | Approach |
|---|---|
| CLI | `click.testing.CliRunner` with `EVALCORE_HOME` pointed at `tmp_path`; every command's happy path, `--json` shape, and error exits |
| Exit codes | `run --min-accuracy` above and below threshold asserts exit 0 / non-zero — this is the CI contract |
| Config precedence | table test asserting flag > env > toml > default for `db_path`, `model`, `concurrency` |
| Permissions | `config.toml` is written `0600`; a `0644` file produces a warning |
| Key bridging | key in toml lands in `os.environ` only when the env var is absent, never overriding it |
| Resolution | name, unique prefix, ambiguous prefix (error naming candidates), and not-found |
| Packaging | CI builds the wheel and asserts `evalcore/api/` and `evalcore/web_dist/index.html` are present and the `eval-core` entry point resolves |
| Launcher | `shellcheck packaging/eval-core.sh`, plus a test running it with a stub `uv` on `PATH` asserting venv creation and the stamp-triggered re-provision |

No test may touch a real `~/.eval-core` or reach the network.

## Out of scope

- Full CLI parity with the web UI (labeling and diff review are bad terminal experiences)
- macOS Keychain storage for the key
- Linux/Windows packaging beyond what PyPI already gives
- Auto-migration of existing databases
- A self-contained PyInstaller binary
