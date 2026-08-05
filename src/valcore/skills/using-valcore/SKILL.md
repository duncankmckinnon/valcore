---
name: using-valcore
description: Use when building, running, or debugging LLM-as-judge evaluations with valcore — authoring evaluators and datasets by hand or by generation, running validation and eval runs, reading agreement metrics, or exporting a judge as a standalone script.
---

# Using valcore

valcore develops, runs, and exports agentic evaluations locally. Everything lives in
one SQLite database and one process — there is no service to sign up for and no data
leaves the machine except the model calls the judge itself makes.

For exact command syntax, use the `valcore-cli` skill. This skill covers what the
pieces are and how they fit.

## The model

**Evaluator** — a named judge. It holds versions and points at one *active* version.

**Evaluator version** — the actual configuration, immutable once frozen:

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

**Dataset** — `columns`, a `label_schema`, and rows. Each row has `data` keyed by
column, and an **optional** `label`. Unlabeled datasets are fine.

**Run** — an evaluator version over a dataset. Two kinds:

- `eval` — score every row. No labels needed.
- `validation` — score every row *and* compare against its label to measure whether the
  judge agrees with you. Every row must be labeled or the run is rejected.

## Compatibility rules

This is where runs most often fail to start. A run requires:

1. **Columns**: `required_columns` must be a **subset** of the dataset's columns. Extra
   dataset columns are fine — the judge simply ignores them.
2. **Label kind**: if the dataset declares a label schema, its kind must equal the
   evaluator's `score_kind`.
3. **Label set**: for categorical scoring, the dataset's labels and the evaluator's
   `score_labels` must be **exactly equal** — not a subset in either direction.

A dataset with no label schema skips checks 2 and 3 and runs against any evaluator. It
just cannot be used for a `validation` run, which needs ground truth.

## The workflow

### 1. Author an evaluator

Two paths, both first-class:

- **By hand** — write the rubric, columns, and score space directly. Use this when you
  already know what you want.
- **By generation** — describe your criteria in natural language and let valcore
  produce a complete draft config, then edit it. Generation returns an editable draft;
  nothing is saved until you save a version.

Design guidance that matters for judge quality:

- State explicit scoring criteria and define **every** label in `instructions`.
- Order `output_fields` so reasoning comes before the score. A judge that commits to a
  score first rationalizes rather than reasons.
- Prefer a categorical score with 3–5 well-defined labels unless the criteria genuinely
  call for a number.

### 2. Build a dataset

Upload a file, author rows by hand, or generate synthetic rows.

Generated rows deliberately mix clearly-passing, clearly-failing, and borderline
examples — roughly a third each. **A judge validated only against good outputs tells
you nothing about whether it catches bad ones.** Keep that mix when authoring by hand.

### 3. Validate the judge

Label a dataset with what you believe the correct answers are, then run a `validation`
run. The result is agreement between the judge and your labels — this is how you find
out whether the rubric works before trusting it.

Use `--min-accuracy` to make a validation run fail (exit code 2) below a threshold,
which is what makes valcore usable in CI.

### 4. Run evaluations

Once the judge agrees with you, run `eval` runs over unlabeled data to score it.

### 5. Export

Export an evaluator version as a standalone Python script that runs the same judge
without valcore installed. Use this to embed a validated judge into another pipeline.

## Practical notes

- The active version is used whenever you do not name one explicitly.
- A version referenced by a run cannot be deleted; that is deliberate, so run history
  stays interpretable.
- Editing a dataset's shape migrates existing rows. A destructive change reports how
  many rows it would damage and refuses unless forced.
- Model access is configured once via the gateway key in the config file.
