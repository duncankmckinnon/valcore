<p align="center">
  <img
    src="https://raw.githubusercontent.com/duncankmckinnon/valcore/main/docs/img/logo.png"
    alt="valcore"
    width="420"
  >
</p>

<p align="center">
  <a href="https://github.com/duncankmckinnon/valcore/actions/workflows/test.yml">
    <img src="https://github.com/duncankmckinnon/valcore/actions/workflows/test.yml/badge.svg" alt="CI">
  </a>
  <a href="https://codecov.io/gh/duncankmckinnon/valcore">
    <img src="https://codecov.io/gh/duncankmckinnon/valcore/graph/badge.svg" alt="codecov">
  </a>
  <a href="https://pypi.org/project/valcore/">
    <img src="https://img.shields.io/pypi/v/valcore" alt="PyPI">
  </a>
  <a href="https://github.com/duncankmckinnon/homebrew-tap">
    <img src="https://img.shields.io/badge/homebrew-duncankmckinnon%2Ftap-orange?logo=homebrew" alt="Homebrew">
  </a>
  <a href="https://pypi.org/project/valcore/">
    <img src="https://img.shields.io/pypi/pyversions/valcore" alt="Python">
  </a>
  <a href="https://github.com/duncankmckinnon/valcore/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License: Apache 2.0">
  </a>
</p>

<!--
Absolute raw.githubusercontent URL rather than a relative path: this README is the
PyPI long_description, and PyPI resolves relative paths against pypi.org, so a
relative image renders broken there. The URL is pinned to main, so it only resolves
once this lands on the default branch.
-->

A small, self-contained local tool for developing, improving, and running agentic
evaluations. Author evaluators in a web UI, run them over datasets from the command
line, and gate CI on their accuracy.

## Install

With Homebrew:

```bash
brew install duncankmckinnon/tap/valcore
```

Or as a `uv` tool:

```bash
uv tool install valcore
```

Either way you get an `valcore` command on your `PATH`.

## Web UI

`valcore serve` opens a dark-themed web UI with four surfaces:

- **Overview** — the landing page, summarizing what you have and pointing to the next
  step.
- **Evaluators** — author, version, and validate LLM-as-judge evaluators.
- **Datasets** — build and edit the datasets evaluators run over, by hand or generated
  from a description.
- **Runs** — inspect completed runs, their metrics, and per-row scores, and compare runs
  against each other.

## Quickstart

Store your gateway API key, then start the app:

```bash
valcore config set-key sk-...
valcore serve
```

`serve` starts the web UI and API on <http://127.0.0.1:8000> and opens a browser
(pass `--no-browser` to skip that, or `--port` to bind elsewhere). Author evaluators
and datasets in the UI, then drive runs from the command line. Both can be written by
hand or generated from a description; a generated result is an editable draft either way.

## Seeding one from the other

An evaluator and a dataset have to agree on columns, so rather than retype that shape you
can seed either one from the other and let the model fill in content you describe.

Generate a dataset from an evaluator version and the dataset always gets that version's
required columns; you can add extra columns by naming them, and per-column notes say what
each should contain. Suggested labels are optional — ask for them when you want the model
to propose ground truth, and the label space comes from the evaluator.

Generate an evaluator from a dataset and it is drafted against that dataset's columns,
with per-column notes saying how each factors into the assessment. The result is an
editable draft, not a saved version, so you review and adjust it before keeping it.

A dataset needs no labels to be scored: an ordinary run just records the judge's output.
Labels are only required for a `validation` run, which compares the judge against them to
measure agreement.

## Models and the gateway

valcore reaches models through the [Pydantic AI Gateway](https://ai.pydantic.dev/gateway/).
That is currently the only route: there is no direct-to-provider client and no
per-provider API key, so a gateway key is required before anything that calls a model
will run.

Model strings are always `gateway/<provider>:<model>`:

```
gateway/anthropic:claude-sonnet-5      # the default
gateway/anthropic:claude-opus-4-5
gateway/anthropic:claude-haiku-4-5
gateway/openai:gpt-5
gateway/google:gemini-2.5-pro
```

Valid providers are `anthropic`, `openai`, `google`, `google-cloud`, `bedrock`, and
`groq`. A string that does not match this shape is rejected before any request is made,
so a bare `claude-sonnet-5` fails fast with a clear error rather than at call time.

The key is stored in `~/.valcore/config.toml` (mode `0600`) and exported as
`PYDANTIC_AI_GATEWAY_API_KEY` when a command runs. An already-exported environment
variable always wins over the stored key, which is what you want in CI:

```bash
export PYDANTIC_AI_GATEWAY_API_KEY=sk-...
```

Run `valcore config set-key` with no argument to be prompted without echoing the key.

Override the default model, highest precedence first: an explicit argument,
`VALCORE_DEFAULT_MODEL`, `model` in `config.toml`, then the built-in default.

> **On other providers.** Routing everything through one gateway keeps model access to a
> single credential and a single validated string format. It also means valcore inherits
> whatever the gateway supports and nothing else. Provider routing is confined to one
> module, so widening this later — direct provider clients, a self-hosted or
> OpenAI-compatible endpoint, local models — is a change to that resolution layer and the
> config schema rather than a change to how evaluators, datasets, or runs work.

## Setup

`valcore serve` shows a setup card on the Overview page listing each key valcore knows
about, whether it is currently set, and what it unlocks. Keys are never entered through
the web UI — no secret crosses HTTP — and are always set from the CLI:

```bash
valcore config set-key                  # required: runs and generation
valcore config set-logfire-token        # optional: sends traces to Logfire
valcore config set-logfire-key          # optional: pushes datasets to Logfire
```

Without the gateway key, generation and runs are unavailable, and the UI shows why. Manual
authoring, dataset upload, editing, hand-labeling, and every export still work with no key
configured at all.

## Commands

| Command | What it does |
| --- | --- |
| `valcore serve` | Serve the web UI and API (`--port`, `--host`, `--no-browser`). |
| `valcore list <evaluators\|datasets\|runs>` | List resources as a table or, with `--json`, as JSON. |
| `valcore run <evaluator> <dataset>` | Run an evaluator version over a dataset. |
| `valcore experiment <evaluator> <dataset>` | Run an evaluator version over a dataset via `pydantic_evals.Dataset.evaluate`. |
| `valcore export <evaluator>` | Export an evaluator (and, with `--dataset`, a dataset) as a Python script or, with `--format json`, a portable eval package. |
| `valcore import <file>` | Import a JSON eval package back into the local database. |
| `valcore config set-key [KEY]` | Store the gateway API key in the config file. |
| `valcore config set-logfire-token [TOKEN]` | Store the Logfire write token in the config file. |
| `valcore config set-logfire-key [KEY]` | Store the Logfire API key in the config file. |
| `valcore config get` | Show the current config (the key is masked unless `--show-key`). |
| `valcore config path` | Print the path to the config file. |
| `valcore config edit` | Open the config file in `$EDITOR`. |
| `valcore logfire push <dataset>` | Push a dataset to Logfire's hosted dataset store. |
| `valcore skills install` | Install the bundled agent skills (`--claude`, `--copilot`, …). |
| `valcore skills list` | Show the bundled skills and where each is installed. |
| `valcore skills uninstall` | Remove the bundled skills from the selected directories. |
| `valcore version` | Print the installed valcore version. |

Evaluators, versions, and datasets are addressable by name or by a unique id prefix;
an ambiguous value is an error that lists the candidates. Pass `--db PATH` on the group
to point at a SQLite database other than the default under `~/.valcore`.

`run` accepts `--version` (defaults to the active version), `--kind`, `--concurrency`,
`--watch` (one line per completed row), `--json`, and `--min-accuracy`. Progress goes to
stderr and results go to stdout, so redirecting stdout yields clean JSON. The CLI talks
to SQLite directly, so `run` works whether or not `serve` is up.

## Portable eval packages

Both an evaluator and a dataset export two ways, as **code** or as **JSON**:

| | Code | JSON |
| --- | --- | --- |
| Evaluator | standalone Python script | `pydantic_ai` `AgentSpec` |
| Dataset | module that builds a `pydantic_evals.Dataset` | `pydantic_evals` `Dataset` |

The JSON forms combine into an **eval package**: a `pydantic_evals` dataset plus a
`pydantic_ai` `AgentSpec`, in one file by default or two with `--split`. Neither half is
invented here — each is the serialization its own framework already defines — and a small
`valcore` block beside them carries the prompt template, required columns, score field, and
tool names that neither foreign format has a place for.

```bash
valcore export my-judge                                  # standalone Python script
valcore export my-judge --format json -o my-judge.json
valcore export --dataset my-data --format json -o my-data.json
valcore export my-judge --dataset my-data --format json --split -o pkg.json
valcore import my-judge.json
```

`valcore export my-judge` with no new flags still emits exactly the Python script it always
has. `--format json` emits the package instead, `--dataset` folds a dataset into either form,
and `--split` writes `pkg.agent.json` and `pkg.dataset.json` side by side rather than one
bundle. `import` reads the JSON form back into your local database; a `.py` export is not
importable.

### Running a package

A `valcore_judge.py` companion module ships beside the JSON and needs no valcore install — it
imports only stdlib `json` and pydantic, rebuilding the agent with `Agent.from_spec`. Register
it as a custom evaluator type and the dataset runs under `pydantic_evals`:

```python
from pydantic_evals import Dataset

from valcore_judge import ValcoreJudge

dataset = Dataset.from_file("my-data.json", custom_evaluator_types=[ValcoreJudge])
report = dataset.evaluate_sync(task)
```

### What the formats can and cannot do

Three limitations are worth stating plainly:

1. Reading a **bundled** package needs `valcore_judge.py`, because `pydantic_evals.Dataset`
   forbids unknown top-level keys and so refuses the bundle's `agent` and `valcore` blocks on
   its own.
2. A dataset exported with an evaluator names `ValcoreJudge` in its `evaluators`, so a bare
   `Dataset.from_file()` — with no `custom_evaluator_types` — cannot read it either.
3. `AgentSpec` has no `tools` field and silently ignores unknown keys, so
   `Agent.from_spec(AgentSpec.from_file("pkg.agent.json"))` builds a **working agent with zero
   tools** without complaint — the tool names live in the `valcore` block, which `AgentSpec`
   drops on load. Use `valcore_judge.py`, which restores them from source inlined into the
   module. If you load the bare spec and wonder why the judge behaves differently, this is why.

JSON is the only config format: the web UI validates and previews a package client-side with
its built-in `JSON.parse`, adding no dependency to do it.

## Agent skills

valcore ships a skill document that teaches a coding agent how to drive it — the data
model, the author/validate/run/export loop, the evaluator-dataset compatibility rules,
and a full CLI reference. Install it into whichever agent you use:

```bash
valcore skills install --claude          # ./.claude/skills/
valcore skills install --claude --copilot
valcore skills install                   # ./.agents/skills/, discoverable by any client
valcore skills install --claude --global # ~/.claude/skills/
```

| Flag | Destination |
| --- | --- |
| *(none)* or `--agents` | `.agents/skills/` |
| `--claude` | `.claude/skills/` |
| `--copilot` | `.github/skills/` |
| `--all` | all of the above |

Flags are additive and nothing is implicit — `--claude --copilot` writes exactly those
two directories and leaves `.agents/` alone. Add `--global` for home-level directories
instead of the current repository.

Copying is the default: an already-identical skill is skipped, and one you have edited
prompts before being overwritten (`--force` to skip the prompt). Use `--symlink` to link
to the packaged copy instead, so upgrading valcore upgrades the installed skill.

Run `valcore skills list` to see what is bundled and where each copy currently lives.

## Using valcore in CI

`run --json` emits a single object with run metadata, metrics, and per-row scores, and
`--min-accuracy` turns a validation run into a pass/fail gate:

```bash
valcore run my-evaluator my-dataset \
  --kind validation \
  --min-accuracy 0.9 \
  --json > run.json
```

Exit codes:

| Exit | Meaning |
| --- | --- |
| `0` | The run finished and, if `--min-accuracy` was set, accuracy met the threshold. |
| `1` | The run failed, or a domain error occurred (printed as `error: <message>` on stderr). |
| `2` | Accuracy fell below `--min-accuracy`. |

`--min-accuracy` requires a categorical accuracy metric; numeric or unlabeled runs have
no accuracy and error rather than silently passing.

## Logfire

`logfire` is an optional extra — install it to get traces:

```bash
uv tool install 'valcore[logfire]'
```

With a Logfire token configured (see [Setup](#setup)), each `valcore run` opens a
`valcore.run` span carrying the run's evaluator version, dataset, and concurrency, with a
`valcore.score_row` child span per row; on close, the run span records its status and each
agreement metric as attributes, so a Logfire query can filter runs by accuracy directly.
The [Pydantic AI Gateway](https://ai.pydantic.dev/gateway/) already reports the LLM calls
themselves — valcore adds only the surrounding run and row context around them, and
deliberately does not re-report the calls, which would double-count tokens and cost.

`valcore experiment <evaluator> <dataset>` runs the same evaluation through
`pydantic_evals.Dataset.evaluate` instead of `run`'s own engine, so it also appears in
Logfire's experiments view. It persists a run the same way `run` does, so it shows up on
the Runs page too. Unlike `run`, it cannot be cancelled, because `Dataset.evaluate` has no
cancellation.

`valcore logfire push <dataset>` publishes a dataset to Logfire's hosted dataset store.
It needs a Logfire API key (see [Setup](#setup)) with the `project:read_datasets` and
`project:write_datasets` scopes.

## `~/.valcore`

All state lives under `~/.valcore` (mode `0700`). Set `VALCORE_HOME` to relocate it.

```
~/.valcore/              0700
  config.toml              0600  gateway key + defaults
  valcore.db             SQLite (plus -wal, -shm)
  logs/                    serve logs
```

## Development

```bash
uv sync                    # install dependencies into a local venv
uv run pytest              # run the test suite
```

The web UI is a Vite + React SPA under `web/`:

```bash
cd web
npm install
npm run dev                # Vite dev server, proxying the API
```

To build the SPA into the wheel, run `npm run build` and copy `web/dist/` into
`src/valcore/web_dist/` before `uv build`; the release workflow does this automatically
on a `v*` tag.
