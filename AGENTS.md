# AGENTS.md

Instructions and orientation for a coding agent working in this repository.
For contribution mechanics written for a human, see
[CONTRIBUTING.md](CONTRIBUTING.md); for user-facing behavior, see
[README.md](README.md).

## Cheat sheet

```bash
uv sync --all-extras              # install backend deps (dev group + extras)
cd web && npm install             # install frontend deps

uv run pytest                     # backend tests
cd web && npx vitest run          # frontend tests
cd web && npx tsc --noEmit        # frontend typecheck

uv run pre-commit run --all-files # ruff check --fix, ruff format, misc hygiene hooks
uv run valcore serve --no-browser # run the API + built-in UI on :8000
```

- Backend source: `src/valcore/`. Frontend source: `web/src/` (Vite + React SPA,
  proxies `/api` to the backend in dev).
- One backend test file per source module — `src/valcore/store.py` is tested by
  `tests/test_store.py`, `src/valcore/api/routes/datasets.py` by
  `tests/test_api_datasets.py`, and so on. Add tests to the matching file.
- This project practices test-first development: write the failing test, watch
  it fail for the right reason, then write the minimal code to pass it.
- If you touch a CLI command, run `python scripts/sync_command_table.py`
  afterward — it syncs README's command table from the canonical one in
  `src/valcore/skills/use-valcore/reference.md`, and `tests/test_docs.py`
  enforces the two stay in sync.
- Full command details and PR expectations are in
  [CONTRIBUTING.md](CONTRIBUTING.md).

## What valcore is

valcore is a small, local-first tool for developing, improving, and running
**agentic evaluations**: agent evaluators that score the outputs of
some other agentic system against a dataset of examples, with a label space
users define. The governing idea is that building a good evaluator is
an iterative loop — author a judge, get labeled data for it to run against,
run it, look at where it disagrees with ground truth, adjust the judge or the
data, repeat — and valcore's job is to make every step of that loop fast and
inspectable without requiring a hosted backend. Everything valcore itself
persists (evaluators, versions, datasets, rows, runs) lives in a single local
SQLite database at `~/.valcore/valcore.db`; nothing about authoring, editing,
or running an evaluation requires a network call. The two places state leaves
the machine are opt-in: a hosted model call through the Pydantic AI Gateway
(or a local CLI, see below, which needs neither), and Logfire, described in
its own section.

## Three things this codebase calls "agents"

The word "agent" refers to three distinct things depending on which part of
the codebase you're in. Keeping them apart matters — conflating them is the
easiest way to misread this code.

### 1. Local CLI agents (`src/valcore/local_cli/`)

valcore reaches hosted models exclusively through the Pydantic AI Gateway
(model strings shaped `gateway/<provider>:<model>`), and that gateway needs a
key. `local_cli/` is the alternative that needs no key at all: it wraps an
already-installed, already-authenticated coding-agent CLI (`claude`, `codex`,
or `cursor-agent`) as a `pydantic_ai.Model`, selected with a `local/<cli>`
model string. `local_cli/bridge_model.py` defines the adapter protocol and the
`CliBridgeModel` that shells out to the CLI's own binary; one adapter module
per CLI (`claude_adapter.py`, `codex_adapter.py`, `cursor_adapter.py`) handles
that tool's specific invocation and output parsing.
`local_cli/__init__.py:resolve_model` is the single seam every other module
routes a model string through — `factory.py`, `generator.py`, and
`datagen.py` never talk to `Agent(...)` directly with a raw string. A
`local/<cli>` route and a `gateway/...` route are otherwise interchangeable
everywhere in valcore: dataset generation, evaluator generation, and judging
all work the same way regardless of which one is configured.

### 2. Evaluators as judge-agents (`factory.py`, `runner.py`, `experiment.py`)

What valcore actually builds and versions is itself an agent: an
`EvaluatorVersion` (instructions, prompt template, output schema, optional
tools and harness capabilities) is a stored, immutable-once-frozen spec, and
`factory.py` turns one into a live `pydantic_ai.Agent` that judges dataset
rows and produces a structured score. `tools.py` and `capabilities.py` are
what an evaluator agent can be given: deterministic row-inspection tools and
the harness capability registry, respectively — both optional, and both
resolved at the same seam local models are.

Two engines run that agent over a dataset: `runner.py` is the primary one
(bounded concurrency, per-row persistence as each result lands, cancellable),
and `experiment.py` is a second path through
`pydantic_evals.Dataset.evaluate`, used when you want the run to also appear
in Logfire's experiments view. Both persist a `Run` the same way. `metrics.py`
scores agreement between the judge's output and a row's label — pure
functions, no I/O — and `schema_migration.py` handles what happens to
existing labels when a dataset's shape changes underneath a running
evaluator.

### 3. The bundled valcore skill (`src/valcore/skills/`, installed via `valcore skills`)

This direction runs the other way: it is not valcore using an agent, it is
valcore teaching an *external* coding agent (Claude Code, Copilot, or any
client that reads `.agents/skills/`) how to drive valcore as a tool — the
data model, the author/validate/run/export loop, evaluator-dataset
compatibility rules, and a full CLI reference. `cli/skills.py` implements
`valcore skills install/list`. If you're an agent reading this file to work
*on* valcore's own source, this is a different document from the one you
want — see `src/valcore/skills/use-valcore/reference.md`, which is written
for an agent operating valcore, not developing it.

## Logfire as external persistence

Logfire is optional and strictly outside valcore's own source of truth — the
local SQLite database is authoritative with or without it — but it is where
state actually leaves the local machine in both directions, not just one:

- **Out**: `tracing.py` is the only module that talks to Logfire's
  instrumentation surface. With a token configured, each `valcore run` opens
  a `valcore.run` span carrying the evaluator version, dataset, and
  concurrency, with a `valcore.score_row` child span per row; the run span
  records its final status and agreement metrics as attributes on close. This
  is observability, not persistence in the sense of being read back — valcore
  never queries these spans itself.
- **In**: `logfire_pull.py` runs an arbitrary SQL query against a Logfire
  project's traces, nests child spans under their parents, samples top-level
  entries, and maps the result into dataset rows — this is how real
  production agent traces become eval data. `logfire_io.py` handles Logfire's
  separate *hosted dataset* store: fetching one into the local SQLite store,
  pushing a local dataset up to it, and the "pull more" / "sync" actions that
  extend an existing local dataset from its Logfire source without
  disturbing rows already there (a SQL-sourced dataset repeats its stored
  query; a hosted-fetch dataset unions in only content not already present).

Three credentials, independently optional, cover these directions: a Logfire
token (write, for tracing), a read key (query traces, list/fetch hosted
datasets), and a write key (push datasets, separate scope from the token).

## Module map

Backend, `src/valcore/`:

| Module | Role |
| --- | --- |
| `models.py` | SQLModel entities, enums, validation |
| `store.py` | SQLite persistence — the local source of truth |
| `settings.py` | Model string validation and resolution, env config |
| `config.py` / `config_io.py` | `~/.valcore/config.toml` layer; eval-package JSON I/O |
| `paths.py` | Filesystem layout under `~/.valcore` |
| `factory.py` | Stored `EvaluatorVersion` → live `pydantic_ai.Agent` |
| `generator.py` | Agents that draft/refine evaluator configs from natural language |
| `datagen.py` | Synthetic dataset row generation with suggested labels |
| `seeding.py` | Pure shape derivation between an evaluator and a dataset |
| `runner.py` / `experiment.py` | The two run-execution engines |
| `metrics.py` | Agreement metrics, pure functions |
| `schema_migration.py` | Dataset shape/label migration on edit |
| `spec.py` | Canonical translation to/from `pydantic_evals`/eval-package formats |
| `export.py` | Render an evaluator or dataset to a standalone runnable script |
| `tools.py` / `capabilities.py` | What an evaluator agent can be given |
| `tracing.py` | The only module that talks to Logfire's instrumentation |
| `logfire_pull.py` / `logfire_io.py` | Logfire traces and hosted datasets in/out |
| `local_cli/` | Local CLI agent adapters and the bridge model |
| `api/` | FastAPI app, routers, dependencies |
| `cli/` | The `valcore` click CLI, a thin shell over the same library |

Frontend, `web/src/`: `pages/` (one per top-level UI surface — Overview,
Evaluators, Datasets, Runs, Compare, Settings), `components/` (the pieces
those pages are built from), `api/` (the typed client every request goes
through).
