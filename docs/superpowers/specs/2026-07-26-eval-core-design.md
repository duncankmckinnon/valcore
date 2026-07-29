# eval-core — Design

**Date:** 2026-07-26
**Status:** Approved, ready for implementation planning

## Purpose

A small, self-contained local tool for developing, improving, and running agentic evaluations.
Three capabilities:

1. **Evaluator generator** — describe criteria, get a Pydantic AI evaluator agent definition
   (prompt, output schema, tools, capabilities) that you can edit in the UI and export as a
   standalone Python script.
2. **Dataset tests** — upload or generate input/output datasets, apply your own scoring, then
   validate an evaluator by running it over the dataset and measuring agreement with your labels.
3. **Eval runner** — run a chosen evaluator version over an input dataset, producing one score
   per row.

Single user, runs locally, no auth.

## Stack

- **Core library:** Python 3.12+, Pydantic AI, `pydantic-ai-harness`, SQLModel over SQLite
- **API:** FastAPI (thin layer over the library; SSE for run progress)
- **Frontend:** Vite + React + TypeScript SPA against the JSON API
- **Models:** Pydantic AI Gateway — a single `PYDANTIC_AI_GATEWAY_API_KEY` env var, cross-provider
  model strings. No provider credentials stored in the app.

Verified against the installed packages (`pydantic-ai` 2.19.0, `pydantic-ai-harness` 0.12.0):
model strings take the form `gateway/<provider>:<model>` (e.g. `gateway/anthropic:claude-sonnet-5`),
with gateway routes for anthropic, openai, google, google-cloud, bedrock, and groq. The API key is
read from the environment by pydantic-ai itself, so no provider object is ever constructed.
`Agent(...)` accepts `capabilities=[...]` directly, and the harness capability constructors expose
the guardrails this design relies on (`FileSystem(root_dir=, allowed_patterns=)`,
`Shell(allowed_commands=, default_timeout=)`). CodeMode requires the `code-mode` extra.

## Architecture

Library core with a thin web layer. `evalcore` imports nothing from FastAPI.

```
eval-core/
  src/evalcore/
    models.py      Evaluator, EvaluatorVersion, Dataset, DatasetRow, Run, RunResult
    factory.py     build_agent(version) -> pydantic_ai.Agent
    tools.py       row-inspection tool registry
    runner.py      async run over rows, bounded concurrency, per-row emit
    metrics.py     agreement metrics on the score field
    export.py      render_script(version) -> standalone .py source
    generator.py   generate + refine agents (structured output)
    datagen.py     dataset row + suggested-label generator
    store.py       SQLite persistence
  api/             FastAPI routes, DTOs, SSE
  web/             Vite + React + TS SPA
  tests/
```

Rationale: "copy as Python script" is only trustworthy if the exported artifact matches what
actually ran, so `factory.build_agent` and `export.render_script` consume the same
`EvaluatorVersion` and are tested against each other. The library boundary also makes the whole
engine testable without an HTTP server, and leaves a CLI possible later at no cost.

Runtime: single SQLite file (`evalcore.db`); `uv run eval-core` serves the API and the built SPA on
`127.0.0.1`; `web/` runs its own dev server during development.

## Data model

```
Evaluator            id, name, description, active_version_id, created_at

EvaluatorVersion     id, evaluator_id, version_name, notes, frozen, created_at
                     model                 gateway model string
                     instructions          system prompt (editable text)
                     prompt_template       user message template w/ {column} refs
                     required_columns      e.g. ["input", "output", "context"]
                     output_fields         ordered OutputField specs — custom per evaluator
                     score_field           name of the designated score field
                     score_kind            categorical{labels:[...]} | numeric{min,max}
                     capabilities          [{name, config}]
                     tools                 [tool names from registry]

Dataset              id, name, description, columns[], label_schema, created_at
DatasetRow           id, dataset_id, idx, data(JSON), label(JSON),
                     suggested_label(JSON), label_reasoning,
                     label_source(manual|accepted|generated), note

Run                  id, kind(validation|eval), version_id, dataset_id, status,
                     concurrency, started_at, finished_at, metrics(JSON), error
RunResult            id, run_id, row_id, output(JSON), score_value, agreement,
                     latency_ms, usage, error
```

`Run.status` ∈ `pending | running | completed | completed_with_errors | cancelled | failed`.

`RunResult.agreement` is null on eval runs and on errored rows. On validation runs it is a boolean
(exact match) for categorical score kinds and the signed numeric delta `score_value - label` for
numeric score kinds.

### Output schemas and the score field

Evaluators declare a **fully custom output schema** and mark exactly one field as `score_field`.
Agreement metrics are computed on that field only; the rest of the schema is stored as structured
detail on each `RunResult`. This keeps arbitrary schemas while preserving generic accuracy /
confusion-matrix / MAE reporting.

The schema is persisted as an ordered list of `OutputField` specs — `{name, type, description,
required, enum_values?, minimum?, maximum?}` where `type` is one of str/int/float/bool/enum — not
as raw JSON Schema. Both directions of the parity requirement depend on this: `factory.py` turns
the specs into a live model via `pydantic.create_model`, and `export.py` renders them as a literal
`class ...(BaseModel)` definition a human can read and edit. Raw JSON Schema round-trips poorly
into readable Python source.

### The dataset owns the label schema

A `Dataset` declares its own `label_schema` (categorical with a label set, or numeric with a
range). A **validation run** checks the evaluator's `score_kind` against it before starting and
refuses with a readable message on mismatch — differing kinds, or categorical label sets that
don't line up. Without this, "run evaluator X against dataset Y" has no defined comparison and
would report meaningless agreement.

An **eval run** has no ground truth and skips the check. That check is the only substantive
difference between the two run kinds.

### `required_columns` is the evaluator/dataset contract

Set by the generator, editable, and validated before a run starts — a missing `context` column is
an error at t=0, not at row 40.

### Version freezing

An `EvaluatorVersion` is editable until a `Run` references it; from then on it is `frozen` and
editing offers "save as new version" instead. This makes run history meaningful without building a
version-control UI. `Evaluator.active_version_id` designates the default version pre-selected for
new runs; saving a new version makes it the active one.

## Evaluator lifecycle

```
criteria text ──▶ generator agent ──▶ draft version (all fields populated)
                                          │
        ┌─────────────────────────────────┤
        │                                 │
   edit fields directly            refine box: natural-language
   (prompt, schema, model,          change requests
    capabilities, tools)                  │
        └────────────▶ field-level diff ◀─┘
                            │
                    save as named version
                            │
              validate vs labeled dataset ──▶ metrics ──▶ iterate
                            │
                    "Copy as Python" ──▶ standalone script
```

**Generation** is one-shot: criteria in, a complete `EvaluatorVersion` draft out (name,
instructions, prompt template, required columns, output schema, score field + kind, capabilities,
tools), every field editable in the form afterward.

**Refinement** takes a natural-language instruction and returns a *complete* config plus the list
of fields it changed. The UI shows a per-field diff against the current draft; accept all or
per field. Returning a whole config rather than a patch means no invalid intermediate state is
representable.

**Export** renders any version to a standalone `.py` file that constructs the equivalent
`pydantic_ai.Agent`, reading the gateway key from the environment.

## Tools and capabilities

Evaluators are config-driven; their tools come from a built-in registry, and capabilities come
from `pydantic-ai-harness`.

**Tool registry (row inspection):** regex/substring search, substring count, JSONPath extract over
row columns, string similarity, numeric compare. Deterministic, no external dependencies, callable
against the row's columns.

**Capabilities**, selected by the generator and toggleable in the UI:

| Capability | Default | Notes |
|---|---|---|
| `CodeMode` | on | Collapses registry tools into one sandboxed `run_code` call; the judge writes Python (Monty sandbox) to loop and aggregate over claims. Needs the `codemode` extra. |
| `SubAgents` | off | Delegate per-criterion sub-checks and aggregate. Multiplies cost and latency per row. |
| `Planning` | off | Structured plan before scoring; useful for complex rubrics. |
| `FileSystem` | off | Root-scoped. Enable only for evals that inspect files. |
| `Shell` | off | Command allowlist, timeout, env masking. Enable only when an eval must run external commands. |

Model-written code executes only inside the Monty sandbox. `FileSystem` and `Shell` are genuine
risk surfaces in a tool that runs generated configs; they are off by default and surface their
guardrail knobs (root path, allowlist, timeout) directly in the config form.

## Datasets

**Upload:** CSV or JSONL. Columns are inferred; the user confirms which column is the label and
declares the `label_schema`.

**Generate:** the user describes the format and desired coverage; the datagen agent produces rows
**and** a suggested label with reasoning for each. The generator is prompted to include both
passing and failing outputs — a judge validated only against good outputs tells you nothing.

**Label:** rows land in a grid showing the suggested label and its reasoning. Accept or override
with keyboard shortcuts. `label_source` records whether a label was hand-entered, an accepted
suggestion, or generated, so ground-truth provenance is never ambiguous.

A dataset with unlabeled rows can still be used for eval runs — just not for validation.

## Runs

`POST /runs` returns a run id immediately. A background asyncio task fans rows out under a
`Semaphore` (default 8, adjustable per run) and writes each `RunResult` to SQLite as it completes.
`GET /runs/{id}/events` streams per-row completions and progress over SSE; a page refresh reads
completed rows from the DB and reattaches, so closing the tab does not lose a run. Cancellation
sets a flag the runner checks before dispatching each row: in-flight rows are allowed to finish and
are persisted, no new rows start, and the run ends as `cancelled`.

Sized for datasets up to a few hundred rows. No queue, no workers, no separate process.

### Error handling

- **Row failure** does not abort the run: the error is stored on the `RunResult`, the run finishes
  as `completed_with_errors`, and a "retry failed rows" action re-runs only those.
- **Schema validation failure** (model output doesn't match `output_schema`) uses Pydantic AI's
  built-in retry once, then records a row error. A high rate here is itself signal that the schema
  is too demanding.
- **Config errors** (bad model string, missing capability extra, invalid schema) are caught when a
  version is *saved*, not when a run starts.
- **Column contract violations** and **label-schema mismatches** are caught before the run starts.

### Metrics

Computed on the score field only.

- categorical → accuracy, per-label precision/recall/F1, confusion matrix, Cohen's κ
- numeric → MAE, RMSE, Pearson and Spearman correlation

### Compare

Two runs over the same dataset, side by side: metric deltas at the top, then a row table filtered
to disagreements showing both outputs, both explanations, and the user's label. This is the loop
that improves an evaluator.

## Security and operations

- Binds `127.0.0.1`, no authentication — a single-user local tool, stated plainly rather than
  implied.
- The gateway key is read from the environment, never written to the database and never inlined
  into exported scripts (exports read it from the environment too).
- Model-written code runs only inside Monty.
- Optional Logfire instrumentation behind an env var, off by default.

## Testing strategy

TDD throughout.

| Layer | Approach |
|---|---|
| Library | `TestModel` / `FunctionModel` — runner, factory, generator, datagen all test without network or spend |
| Metrics | Unit tests against hand-built confusion matrices and known-correlation numeric pairs |
| **Factory/export parity** | For a given version, the agent from `factory.build_agent` and the agent defined by `export.render_script`'s output must match on model, instructions, output schema, tools, and capabilities |
| Store | Round-trip and migration tests against a temp SQLite file |
| API | httpx ASGI transport; includes SSE stream and cancellation |
| Frontend | Vitest + React Testing Library for the labeling grid and diff view |

No end-to-end browser layer for now.

## Implementation sequencing

The scope spans three features and a frontend, so it should be built in dependency order rather
than as one pass. Suggested stages, each independently useful and testable:

1. **Core models + store + factory + export** — including the parity test. Nothing runs yet, but
   the load-bearing abstraction is proven.
2. **Runner + metrics** — run a hand-written version over a hand-written dataset in a test.
3. **API + SSE** over stages 1–2.
4. **Generator + refine agents.**
5. **Dataset upload + datagen.**
6. **React SPA** — evaluator editor, labeling grid, run views, compare.

## Explicitly out of scope

- Multi-user, auth, or hosted deployment
- Durable job queue / worker processes / crash resume
- Trace-shaped (span/message/tool-call) dataset rows
- Arbitrary model-authored Python outside the Monty sandbox
- A CLI (the library boundary leaves this cheap to add later)
- Per-field comparators or LLM-based output comparison
