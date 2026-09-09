# Contributing to valcore

valcore is a local-first tool for developing, improving, and running agentic
evaluations. This document covers the practical mechanics of contributing code.
For the project's architecture and the philosophy behind it, see
[AGENTS.md](AGENTS.md); for user-facing behavior, see [README.md](README.md).

## Setup

The backend is a `uv`-managed Python package; the web UI is a separate Vite +
React SPA.

```bash
uv sync --all-extras      # installs the dev dependency group plus every extra (e.g. logfire)
cd web && npm install
```

`uv sync` installs the `dev` dependency group by default (pytest, ruff,
pre-commit, …); `--all-extras` additionally pulls in optional extras like
`logfire`, which some tests exercise.

Install the git hooks once so formatting and linting run automatically:

```bash
uv run pre-commit install
```

## Running the app locally

```bash
uv run valcore serve --no-browser   # API + built-in web UI on :8000
```

To iterate on the SPA with hot reload instead, run the API and the Vite dev
server side by side — `web/vite.config.ts` proxies `/api` to `127.0.0.1:8000`:

```bash
uv run valcore serve --no-browser   # terminal 1
cd web && npm run dev               # terminal 2, :5173
```

## Tests

```bash
uv run pytest              # backend: routes, store, CLI, generation, Logfire I/O, ...
cd web && npx vitest run   # frontend: components and pages
cd web && npx tsc --noEmit # frontend: typecheck (also runs as part of `npm run build`)
```

The backend suite has one test file per source module (`store.py` →
`tests/test_store.py`, `src/valcore/api/routes/datasets.py` →
`tests/test_api_datasets.py`, and so on) — add new tests alongside the module
they cover rather than starting a new grouping. Tests are written test-first:
add the failing test for the behavior you want, watch it fail for the right
reason, then write the minimal code that passes it. `tests/test_local_cli_live.py`
is the one file that talks to real local CLIs (`claude`, `codex`,
`cursor-agent`) instead of stubbing them — it is skipped unless the relevant
CLI is actually installed and authenticated, so it will not fail your PR for
lacking one.

Frontend components ship alongside a co-located `*.test.tsx`, exercised with
Testing Library — assert on rendered behavior (labels, roles, values a user
would see), not on implementation details.

## Formatting and linting

Ruff (configured for a 100-column line length) handles both linting and
formatting; the pre-commit hooks run `ruff check --fix` and `ruff format`
automatically on commit. `pytest` runs on push, not on every commit — it is
fast but not instant, and a slow `git commit` is how people learn to pass
`--no-verify`, which this project asks you not to do.

If you ever need to run everything CI runs, in order:

```bash
uv run pre-commit run --all-files
uv run pytest -q
cd web && npm run build && npx vitest run
```

## Code style

- Module and function docstrings explain *why*, not *what* — a reader can see
  what a function does from its name and body; the docstring earns its place
  by stating a non-obvious constraint, invariant, or the reason a design
  choice was made a particular way. Most modules in this codebase open with a
  short docstring doing exactly that; read a few nearby before adding one of
  your own.
- Inline comments are rare and reserved for the same reason: a hidden gotcha,
  not a restatement of the next line.
- Don't add error handling, fallbacks, or abstraction for cases that can't
  happen here — trust the internal contracts the store, models, and API
  layers already establish, and validate only at real boundaries (user input,
  an external API response).
- If you rename or add a CLI command, run `python scripts/sync_command_table.py`
  to update the command table in README.md from the canonical one in
  `src/valcore/skills/use-valcore/reference.md` (that file is the source of
  truth; README carries a copy for readers who never install the skill).
  `tests/test_docs.py` runs the script with `--check` and fails if the two
  have drifted.

## Branches, commits, and PRs

Branch names are short and descriptive; a `feat/`, `fix/`, or `docs/` prefix
is common in this repo's history but not enforced. Commit messages favor
explaining *why* a change was made over restating *what* changed line by
line.

Every PR runs three CI jobs (`.github/workflows/test.yml`): `pre-commit
run --all-files` (lint), the backend test matrix across Python 3.11–3.14, and
the web build + `vitest run`. All three must pass before merge. There is no
separate release step to run yourself — pushing a `v*` tag on `main` triggers
`.github/workflows/release.yml`, which builds the SPA, builds the wheel and
sdist, and publishes to PyPI (and, for a non-prerelease version, updates the
Homebrew tap).

## License

By contributing, you agree your contribution is licensed under this project's
[Apache 2.0 license](LICENSE).
