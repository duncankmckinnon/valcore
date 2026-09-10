---
name: use-valcore
description: Use when building, running, or debugging LLM-as-judge evaluations with valcore — choosing a model route (a Pydantic AI gateway key, or a local Claude Code/Codex/Cursor CLI), authoring evaluators and datasets by hand or by generation, running validation and eval runs, reading agreement metrics, or exporting a judge as a standalone script.
---

# Using valcore

valcore develops, runs, and exports agentic evaluations locally. Everything lives in one
SQLite database and one process — there is no service to sign up for, and no data leaves
the machine except the model calls the judge itself makes, which go through either a
hosted gateway or a coding CLI already logged in on this machine.

[reference.md](reference.md) is the complete CLI reference: every command, flag,
configuration key, environment variable, and exit code. Read it when you need exact
syntax.

[installation.md](installation.md) is the headless setup checklist: which keys are
required for what, and how to confirm a `local/<cli>` route's CLI is actually installed
and logged in before trusting a run. Read it when driving valcore non-interactively and
something needs to be verified before it runs, or when a run fails in a way that looks
environmental rather than a config or data problem.

## Setup: choosing a model route

A model string names one of two routes.

**Gateway** reaches a hosted model through the [Pydantic AI](https://ai.pydantic.dev)
gateway. There is no direct-to-provider path and no per-provider API key, so a gateway key
is required before a hosted model will run.

**Local CLI** reuses a coding-agent CLI already installed and logged in on this machine,
and needs no key at all. Prefer it when the user has no gateway key — it is usually the
difference between "cannot run anything" and a working evaluation loop.

### Gateway model strings

Hosted model strings are always `gateway/<provider>:<model>`:

```
gateway/anthropic:claude-sonnet-5      # the default
gateway/anthropic:claude-opus-4-5
gateway/anthropic:claude-haiku-4-5
gateway/openai:gpt-5
gateway/google:gemini-2.5-pro
```

Valid providers are `anthropic`, `openai`, `google`, `google-cloud`, `bedrock`, and
`groq`. A bare `claude-sonnet-5` or `openai:gpt-5` is rejected up front with a
`ConfigError`.

### Local CLI model strings

A local route names a CLI and nothing else — there is no model name after it, because
each route runs that CLI's own binary, which answers with whichever model it is already
configured to use:

```
local/claude    # the `claude` binary (Claude Code)
local/codex     # the `codex` binary (Codex CLI)
local/cursor    # the `cursor-agent` binary (Cursor CLI)
```

The binary must be on `PATH` and already authenticated; valcore does not manage the login,
and does not check any of this before a run — see [installation.md](installation.md) for
which binary backs which route name and how to verify one. Set a route as the default, or
name it as a version's `model`:

```bash
valcore config set local_cli_default claude   # default for new versions and generation
valcore config unset local_cli_default        # back to the gateway
```

**A local model gives up three things**, and valcore enforces each rather than failing at
call time:

- **No tools.** A version that sets `tools` *and* a local model is rejected when saved.
- **No harness capabilities.** `capabilities` are not attached for a local model; the CLI
  brings its own tooling.
- **No standalone export.** `valcore export` to Python refuses a local model — the
  rendered script is valcore-free, so it has no way to reach a local CLI.

Prefer an explicit gateway model on any version whose validation number is a release gate:
a local CLI answers with whatever model it currently defaults to, which can change when
the tool updates.

### Configure the gateway key

```bash
valcore config set-key          # prompts, input hidden
valcore config set-key sk-...   # or pass it directly
```

This writes `gateway_api_key` to `~/.valcore/config.toml` (or `$VALCORE_HOME/config.toml`)
atomically with mode `0600`. Verify with `valcore config get`, which masks the key unless
you pass `--show-key`.

At startup valcore exports the stored key as `PYDANTIC_AI_GATEWAY_API_KEY`. **An
explicitly exported environment variable always wins** — the stored key is only applied
when that variable is unset. So for CI, export it directly and skip the config file:

```bash
export PYDANTIC_AI_GATEWAY_API_KEY=sk-...
```

If the config file is group- or world-readable, valcore warns and tells you to
`chmod 600` it, but still loads it.

### Which model actually resolves

The built-in default is `gateway/anthropic:claude-sonnet-5`. Override it, highest
precedence first:

1. an explicit argument
2. `VALCORE_DEFAULT_MODEL` in the environment
3. `local_cli_default` in `config.toml`
4. `model` in `config.toml`
5. the built-in default

Note rung 3: a stored local CLI outranks a stored gateway model string.
`VALCORE_DEFAULT_MODEL` outranks both, so a stored `local_cli_default` can be stale
relative to what really runs — `valcore config get` shows what is stored, and the
Overview page shows what resolves.

`VALCORE_DEFAULT_CONCURRENCY` and `VALCORE_DB_PATH` follow the same
argument/env/file/default order.

### Setting any config key

`valcore config set KEY VALUE` and `valcore config unset KEY` cover every key in
`config.toml`, including the ones with no named command (`model`, `local_cli_default`,
`port`, `concurrency`, `db_path`). Values are validated before they are written.

## The model

**Evaluator** — a named judge. It holds versions and points at one *active* version.

**Evaluator version** — the actual configuration:

| Field | Meaning |
|---|---|
| `instructions` | Rubric-style system prompt. Defines every label explicitly. |
| `prompt_template` | Per-row user prompt using `{column}` placeholders. |
| `required_columns` | Every column the judge needs. Each must appear in the template. |
| `output_fields` | Ordered. Put the reasoning field *before* the score field. |
| `score_field` | Which output field carries the score. Must name one of `output_fields`. |
| `score_kind` | `categorical` or `numeric`. |
| `score_labels` | The label set, for categorical scoring. |
| `score_minimum` / `score_maximum` | The bounds, for numeric scoring. |
| `capabilities` | `CodeMode`, `SubAgents`, `Planning`, `FileSystem`, `Shell`. Ignored for a local CLI model. |
| `tools` | Registry tools the rubric actually needs. Rejected with a local CLI model. |

**Dataset** — `columns` and rows. Each row has `data` keyed by column. Ground truth lives
separately: a dataset can have any number of **label sets** (named annotation contracts,
categorical or numeric), each with its own per-row **annotations**. A dataset with no
label sets is fine — it just cannot back a `validation` run.

**Run** — an evaluator version over a dataset. Two kinds:

- `eval` — score every row. No labels needed.
- `validation` — score every row that has a valid label under the matching label set, and
  compare against it, measuring whether the judge agrees with you. Rows without a valid
  label are skipped, not rejected — the run scores whatever subset has ground truth, and
  only fails outright if none do.

## Compatibility rules

This is where runs most often fail to start. A run requires:

1. **Columns**: `required_columns` must be a **subset** of the dataset's columns. Extra
   dataset columns are fine — the judge ignores them.
2. **Label kind**: `validation` needs a label set on the dataset whose kind equals the
   evaluator's `score_kind`.
3. **Label set**: for categorical scoring, one of the dataset's label sets' labels and the
   evaluator's `score_labels` must be **exactly equal** — not a subset in either direction.
   That label set is the one used as ground truth.

A dataset with **no label sets at all** skips checks 2 and 3 — with no label space
declared, there is nothing to reconcile with the score space. It still must satisfy
check 1: the evaluator's `required_columns` must be present. Such a dataset runs against
any evaluator whose columns it covers; it just cannot back a `validation` run (no label
set exists to match). A dataset that **has** label sets, none of which match the
evaluator's score contract, fails checks 2/3 outright — a real mismatch, not merely "no
ground truth declared."

Labels are therefore **optional** at the dataset level. An `eval` run needs no label set
at all — a label set is required only for a validation run, and even then not every row
need be labeled under it. It scores whatever subset has valid ground truth, failing only
if none do.

## The workflow

### 1. Author an evaluator

Two paths, both first-class:

- **By hand** — write the rubric, columns, and score space directly. Use this when you
  already know what you want.
- **By generation** — describe your criteria in natural language and let valcore produce
  a complete draft config, then edit it. Generation returns an editable draft; nothing is
  saved until you save a version.
- **By generation, seeded from a dataset** — generate an **evaluator from a dataset** so
  its shape comes from that dataset's columns. Per-column notes say how each column factors
  into the assessment; a column described as irrelevant ends up in neither
  `required_columns` nor the `prompt_template`. The model never invents columns. The result
  is still an editable draft — nothing is persisted until you save a version.

Design guidance that matters for judge quality:

- State explicit scoring criteria and define **every** label in `instructions`.
- Order `output_fields` so reasoning comes before the score. A judge that commits to a
  score first rationalizes rather than reasons.
- Prefer a categorical score with 3–5 well-defined labels unless the criteria genuinely
  call for a number.

### 2. Build a dataset

Upload a file, author rows by hand, generate synthetic rows, or pull from Logfire
(`valcore logfire pull` for a SQL query, `valcore logfire fetch` for a hosted dataset,
or the Logfire tab in the Datasets UI). A SQL pull runs your query, nests child spans
that the result actually contained under their parents, samples top-level entries, and
stores descendants as a JSON `children` column when any sampled tree has children.
Include `{children}` in a judge's `required_columns` when the eval should see the subtree.
A hosted fetch copies cases as they already exist in the source project.

To make test data for an evaluator you already have, generate a
**dataset from an evaluator version**. It always receives that version's
`required_columns` — shape
derives from the source, and instructions cannot remove them. Extra columns are allowed
but must be typed in explicitly; the model never infers them. Per-column notes say what
each column should contain. The result is compatible with the source evaluator by
construction.

Suggested labels are **optional** here. When you include them, the label space comes from
the evaluator — you supply only guidance on *how to assign* labels, never what the labels
are. Leave them out and the dataset carries no ground truth, which is fine for `eval` runs.

Generation imposes no distribution of its own — it follows what you ask for. **A judge
validated only against good outputs tells you nothing about whether it catches bad ones**,
so make sure failing and borderline cases are represented, and keep the same balance when
authoring by hand.

Two ways to get them. Describe the balance you want in `instructions`, or prescribe it
exactly with `label_mix` — a map of label to its share of `count`, summing to 1.0:

```json
{"label_mix": {"pass": 0.34, "fail": 0.33, "borderline": 0.33}}
```

Shares become whole row counts before the model sees them, so it is told "8 rows labeled
`fail`" rather than a percentage. Naming only some labels is fine; the ones you omit get
no rows. `label_mix` needs a categorical label space, and on `generate-from-version` it
needs `include_labels: true`.

Whatever you asked for is **stored against the dataset**, so you never have to reconstruct
it. `GET /api/datasets/{id}/generation` returns the instructions, column notes, label mix,
guidance, and count that produced the rows — or `null` for a dataset that was uploaded or
created blank. It also records `source_version_id` when the dataset was seeded from an
evaluator, as provenance only: that version may since have changed or been deleted, so
nothing derives shape from it.

That makes topping a dataset up cheap. `POST /api/datasets/{id}/generate-rows` appends more
rows and falls back to the stored settings, so repeating the same ask needs only a count:

```json
{"count": 10}
```

Any field you do pass overrides the stored one and becomes the new stored ask, so the next
top-up repeats what actually ran. Shape is never overridable — the dataset's own columns and
label space always apply, which is what keeps the new rows compatible with the existing ones
and with any evaluator already running against them.

### 3. Validate the judge

Label a dataset with what you believe the correct answers are, then run a `validation`
run. The result is agreement between the judge and your labels — this is how you find out
whether the rubric works before trusting it.

Use `--min-accuracy` to make a validation run fail (exit code 2) below a threshold, which
is what makes valcore usable in CI.

### 4. Run evaluations

Once the judge agrees with you, run `eval` runs over unlabeled data to score it.

### 5. Export

Export an evaluator version as a standalone Python script that runs the same judge
without valcore installed. Use this to embed a validated judge into another pipeline.

This requires a `gateway/...` model: the rendered script has no valcore dependency, so a
local CLI model is refused with a `ContractError`.

## Practical notes

- The active version is used whenever you do not name one explicitly.
- A version referenced by a run cannot be deleted; that is deliberate, so run history
  stays interpretable.
- Editing a dataset's shape (renaming or changing columns) remaps existing rows' data
  automatically.
