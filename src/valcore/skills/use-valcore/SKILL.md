---
name: use-valcore
description: Use when building, running, or debugging LLM-as-judge evaluations with valcore — setting up the Pydantic AI gateway key, authoring evaluators and datasets by hand or by generation, running validation and eval runs, reading agreement metrics, or exporting a judge as a standalone script.
---

# Using valcore

valcore develops, runs, and exports agentic evaluations locally. Everything lives in one
SQLite database and one process — there is no service to sign up for, and no data leaves
the machine except the model calls the judge itself makes.

[reference.md](reference.md) is the complete CLI reference: every command, flag,
configuration key, environment variable, and exit code. Read it when you need exact
syntax.

## Setup: the Pydantic AI gateway

**valcore talks to models exclusively through the [Pydantic AI](https://ai.pydantic.dev)
gateway.** There is no direct-to-provider path and no per-provider API key. Nothing runs
until a gateway key is configured.

Model strings are always `gateway/<provider>:<model>`:

```
gateway/anthropic:claude-sonnet-5      # the default
gateway/anthropic:claude-opus-4-5
gateway/anthropic:claude-haiku-4-5
gateway/openai:gpt-5
gateway/google:gemini-2.5-pro
```

Valid providers are `anthropic`, `openai`, `google`, `google-cloud`, `bedrock`, and
`groq`. Anything not matching `gateway/<provider>:<model>` is rejected up front with a
`ConfigError` — a bare `claude-sonnet-5` or `openai:gpt-5` will not work.

### Configure the key

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

### Choosing a model

The default is `gateway/anthropic:claude-sonnet-5`. Override it, highest precedence
first:

1. an explicit argument
2. `VALCORE_DEFAULT_MODEL` in the environment
3. `model` in `config.toml`
4. the built-in default

The same order governs `VALCORE_DEFAULT_CONCURRENCY` and `VALCORE_DB_PATH`.

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
| `capabilities` | `CodeMode`, `SubAgents`, `Planning`, `FileSystem`, `Shell`. |
| `tools` | Registry tools the rubric actually needs. |

**Dataset** — `columns`, a `label_schema`, and rows. Each row has `data` keyed by column,
and an **optional** `label`. Unlabeled datasets are fine.

**Run** — an evaluator version over a dataset. Two kinds:

- `eval` — score every row. No labels needed.
- `validation` — score every row *and* compare against its label, measuring whether the
  judge agrees with you. Every row must be labeled or the run is rejected.

## Compatibility rules

This is where runs most often fail to start. A run requires:

1. **Columns**: `required_columns` must be a **subset** of the dataset's columns. Extra
   dataset columns are fine — the judge ignores them.
2. **Label kind**: if the dataset declares a label schema, its kind must equal the
   evaluator's `score_kind`.
3. **Label set**: for categorical scoring, the dataset's labels and the evaluator's
   `score_labels` must be **exactly equal** — not a subset in either direction.

A dataset with an empty label schema skips checks 2 and 3 — with no label space declared,
there is nothing to reconcile with the score space. It still must satisfy check 1: the
evaluator's `required_columns` must be present. Such a dataset runs against any evaluator
whose columns it covers; it just cannot back a `validation` run, which needs ground truth.

Labels are therefore **optional**. An `eval` run scores unlabeled rows fine — labels are
required only for a `validation` run, which measures whether the judge agrees with you.

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

Upload a file, author rows by hand, or generate synthetic rows.

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

Generated rows deliberately mix clearly-passing, clearly-failing, and borderline examples
— roughly a third each. **A judge validated only against good outputs tells you nothing
about whether it catches bad ones.** Keep that mix when authoring by hand.

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

## Practical notes

- The active version is used whenever you do not name one explicitly.
- A version referenced by a run cannot be deleted; that is deliberate, so run history
  stays interpretable.
- Editing a dataset's shape migrates existing rows. A destructive change reports how many
  rows it would damage and refuses unless forced.
