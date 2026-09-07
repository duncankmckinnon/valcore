# valcore CLI reference

Syntax only. Concepts, workflow, and gateway setup are in [SKILL.md](SKILL.md). Setup and
environment verification for headless use are in [installation.md](installation.md).

This file is the source of truth for the command list: `README.md`'s own summary table
is generated from the one below and a test fails if the two drift apart. Add or change a
command here first, then regenerate with `scripts/sync_command_table.py`.

## Contents

- [Command summary](#command-summary) — every command in one table
- [Global](#global) — `--db`, `--version`, database resolution
- [`valcore version`](#valcore-version)
- [`valcore serve`](#valcore-serve) — run the web app and API
- [`valcore list`](#valcore-list-evaluatorsdatasetsruns) — evaluators, datasets, runs
- [`valcore run`](#valcore-run-evaluator-dataset) — validation and eval runs
- [`valcore experiment`](#valcore-experiment-evaluator-dataset) — the `pydantic_evals` engine over the same data
- [`valcore export`](#valcore-export-evaluator) — standalone judge script or portable eval package
- [`valcore import`](#valcore-import-path) — load a portable eval package back in
- [`valcore config`](#valcore-config) — gateway key and defaults
- [`valcore skills`](#valcore-skills) — install these skills into agent directories
- [`valcore logfire`](#valcore-logfire) — pull from a query or hosted dataset; push to hosted store
- [Not in the CLI](#not-in-the-cli) — seeded generation is API and web only
- [Configuration](#configuration) — `config.toml` keys, `VALCORE_*` environment variables
- [Exit codes](#exit-codes)

## Command summary

<!-- COMMANDS:START -->
| Command | What it does |
| --- | --- |
| `valcore serve` | Serve the web UI and API (`--port`, `--host`, `--no-browser`). |
| `valcore list <evaluators\|datasets\|runs>` | List resources as a table or, with `--json`, as JSON. |
| `valcore run <evaluator> <dataset>` | Run an evaluator version over a dataset. |
| `valcore experiment <evaluator> <dataset>` | Run an evaluator version over a dataset via `pydantic_evals.Dataset.evaluate`. |
| `valcore export <evaluator>` | Export an evaluator (and, with `--dataset`, a dataset) as a Python script or, with `--format json`, a portable eval package. |
| `valcore import <file>` | Import a JSON eval package back into the local database. |
| `valcore config set-key [KEY]` | Store the gateway API key in the config file. |
| `valcore config set-logfire-token [TOKEN]` | Store the Logfire tracing token in the config file. |
| `valcore config set-logfire-key [KEY]` | Store one Logfire API key as both the read and write keys. |
| `valcore config set-logfire-read-key [KEY]` | Store the Logfire read key (query traces and hosted datasets in the source project). |
| `valcore config set-logfire-write-key [KEY]` | Store the Logfire write key (push datasets to the valcore project). |
| `valcore config set-logfire-explore-url [URL]` | Optional fallback SQL Workbench URL if the read key cannot resolve the project. |
| `valcore config get` | Show the current config (the key is masked unless `--show-key`). |
| `valcore config path` | Print the path to the config file. |
| `valcore config edit` | Open the config file in `$EDITOR`. |
| `valcore logfire pull` | Create a dataset from a Logfire SQL query (`--sql` or `--sql-file`, `--name`, `--count`). |
| `valcore logfire list` | List hosted datasets in the source Logfire project. |
| `valcore logfire fetch <name>` | Create a local dataset from a hosted Logfire dataset. |
| `valcore logfire push <dataset>` | Push a dataset to Logfire's hosted dataset store. |
| `valcore skills install` | Install the bundled agent skills (`--claude`, `--copilot`, …). |
| `valcore skills list` | Show the bundled skills and where each is installed. |
| `valcore skills uninstall` | Remove the bundled skills from the selected directories. |
| `valcore version` | Print the installed valcore version. |
<!-- COMMANDS:END -->

## Global

```
valcore [--db FILE] [--version] COMMAND [ARGS]...
```

| Option | Meaning |
|---|---|
| `--db FILE` | Override the SQLite database path. |
| `--version` | Print the version and exit. Same string as `valcore version`. |

**Watch the name collision:** at the top level `--version` prints the tool version, but
on `run` and `export` `--version` names an *evaluator version*. Different flags at
different levels.

### Database resolution

The database path comes from settings unless `--db` overrides it. If a `valcore.db`
exists in the working directory but is **not** the active database, valcore prints a note
on stderr telling you to pass `--db valcore.db`. It does not switch silently.

## Commands

### `valcore version`

Prints the installed version. Falls back to the built-in version file when running from a
source checkout with nothing installed, so it does not crash.

### `valcore serve`

Serves the web app and API.

| Option | Default |
|---|---|
| `--port INTEGER` | 8000, or the configured port |
| `--host TEXT` | `127.0.0.1` |
| `--no-browser` | opens a browser otherwise |

### `valcore list {evaluators|datasets|runs}`

| Option | Meaning |
|---|---|
| `--json` | Emit JSON instead of a table. |

### `valcore run EVALUATOR DATASET`

Runs an evaluator version over a dataset.

| Option | Meaning |
|---|---|
| `--version TEXT` | Version name. Defaults to the evaluator's active version. |
| `--kind [validation\|eval]` | Validate against labels, or score a dataset. |
| `--concurrency INTEGER` | Max concurrent rows. |
| `--json` | Emit JSON results to stdout. |
| `--watch` | Print one line per completed row. |
| `--min-accuracy FLOAT` | Exit 2 below this threshold. |

`EVALUATOR` and `DATASET` resolve by exact name first, then by unique id prefix.

`--kind validation` requires every row to carry a label and fails outright if any row is
unlabeled. `--kind validation --min-accuracy 0.9` is the CI pattern.

### `valcore experiment EVALUATOR DATASET`

A second engine over the same data as `run`, driven by `pydantic_evals.Dataset.evaluate`
instead of valcore's own runner. Interchangeable with `run` at the CLI level, except it
has no `--watch` and no cancellation, because `Dataset.evaluate` offers neither.

| Option | Meaning |
|---|---|
| `--version TEXT` | Version name. Defaults to the active version. |
| `--concurrency INTEGER` | Max concurrent rows. |
| `--json` | Emit JSON results to stdout. |

Always runs as `validation` kind; there is no `--kind` here.

### `valcore export [EVALUATOR]`

Exports an evaluator version, a dataset, or both, as Python code or as an eval-package
JSON document.

| Option | Meaning |
|---|---|
| `--version TEXT` | Version name. Defaults to the active version. |
| `-o, --output FILE` | Write to a file (or, with `--split`, its stem's siblings) instead of stdout. |
| `--format [code\|json]` | `code` (default) emits a standalone Python script; `json` emits the eval-package format. |
| `--dataset TEXT` | Include this dataset in the export. |
| `--split` | With `--format json`, write two files (`<stem>.agent.json`, `<stem>.dataset.json`) instead of one bundle. Needs `-o`; meaningless with `--format code`. |

`EVALUATOR` is optional: `valcore export --dataset my-data --format json` exports a
dataset with no evaluator. At least one of `EVALUATOR` or `--dataset` must be given.

### `valcore import PATH`

Imports an eval-package JSON document (from `valcore export --format json`), creating its
dataset and/or evaluator version in the local database.

| Option | Meaning |
|---|---|
| `--name TEXT` | Override the imported dataset's name. |

A `.py` export is not importable — only the JSON form round-trips. The evaluator's
version is validated before anything is written, so a package that fails validation
persists nothing, not even its dataset half.

### `valcore config`

| Subcommand | Purpose |
|---|---|
| `set-key [KEY]` | Store the Pydantic AI gateway key. Prompts hidden if omitted. |
| `set-logfire-token [TOKEN]` | Store the Logfire tracing token. |
| `set-logfire-key [KEY]` | Store one Logfire API key as both read and write. |
| `set-logfire-read-key [KEY]` | Store the Logfire read key (query traces and hosted datasets in the source project). |
| `set-logfire-write-key [KEY]` | Store the Logfire write key (push datasets to the valcore project). |
| `set-logfire-explore-url [URL]` | Optional fallback SQL Workbench URL if the read key cannot resolve the project. |
| `get [--show-key] [--json]` | Show config. The key is masked unless `--show-key`. |
| `path` | Print the config file path. |
| `edit` | Open the config file in `$EDITOR`. |

### `valcore skills`

Installs the skill documents shipped inside the package into agent directories.

```
valcore skills install [AGENT FLAGS] [--global] [--symlink] [--force]
valcore skills uninstall [AGENT FLAGS] [--global]
valcore skills list [--global]
```

| Agent flag | Destination (repo) | Destination (`--global`) |
|---|---|---|
| *(none)* | `.agents/skills/` | `~/.agents/skills/` |
| `--agents` | `.agents/skills/` | `~/.agents/skills/` |
| `--claude` | `.claude/skills/` | `~/.claude/skills/` |
| `--copilot` | `.github/skills/` | `~/.github/skills/` |
| `--all` | every directory above | every directory above |

Flags are additive and nothing is implicit — `--claude --copilot` writes exactly those two
directories and does not also touch `.agents/`.

| Option | Meaning |
|---|---|
| `--symlink` | Link to the packaged skills so upgrades apply automatically. |
| `--force` | Overwrite differing skills without prompting. |
| `--global` | Use home-level directories instead of repo-level. |

Copy mode skips a skill whose content is already byte-identical, and prompts before
overwriting one you have edited. `--symlink` always replaces.

### `valcore logfire`

| Subcommand | Purpose |
|---|---|
| `pull --sql SQL --name NAME --count N` | Create a local dataset from a Logfire query. Also `--sql-file`, `--seed`, `--min-timestamp`, `--max-timestamp`, `--label-column`, `--label-schema`. |
| `list` | List hosted datasets in the source project. |
| `fetch NAME` | Create a local dataset from a hosted dataset. `--name` and `--description` override the local copy. |
| `push DATASET` | Publish a dataset to Logfire's hosted store. |

## Not in the CLI

Seeded generation — deriving a dataset's shape from an evaluator version, or an
evaluator's columns from a dataset — is exposed only through the API and web app, not the
CLI. There is no command or flag for it here; do not go looking for one. See
[SKILL.md](SKILL.md) for the workflow.

## Configuration

`~/.valcore/config.toml`, or `$VALCORE_HOME/config.toml`. Written with mode `0600`.

| Key | Meaning |
|---|---|
| `gateway_api_key` | Pydantic AI gateway key, exported as `PYDANTIC_AI_GATEWAY_API_KEY`. |
| `model` | Default model, as `gateway/<provider>:<model>`. |
| `port` | Default port for `serve`. |
| `concurrency` | Default max concurrent rows. |
| `db_path` | Default database path. |
| `local_cli_default` | One of `claude`, `codex`, `cursor`. Overrides `model` with `local/<name>` when set. Set through the web UI's Settings page, not the CLI. |
| `logfire_token` | Write token for tracing valcore's own project. |
| `logfire_read_key` | API key for querying traces and hosted datasets in the source-trace project. |
| `logfire_write_key` | API key for pushing datasets to the valcore project. |
| `logfire_api_key` | Legacy combined API key; still loaded as both read and write. |
| `logfire_explore_url` | Fallback SQL Workbench URL if the read key cannot resolve the project. |

### Environment variables

| Variable | Effect |
|---|---|
| `PYDANTIC_AI_GATEWAY_API_KEY` | Gateway key. Overrides the stored one when set. |
| `VALCORE_HOME` | Home directory. Defaults to `~/.valcore`. |
| `VALCORE_DEFAULT_MODEL` | Default model. |
| `VALCORE_DEFAULT_CONCURRENCY` | Default max concurrent rows. |
| `VALCORE_DB_PATH` | Database path. |
| `VALCORE_LOGFIRE_ENABLED` | Enable Logfire instrumentation. |

Precedence, highest first: explicit argument, `VALCORE_*` environment variable,
`config.toml`, built-in default.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success. |
| 1 | Domain error. Printed as `error: <message>` on stderr. |
| 2 | `--min-accuracy` threshold not met. |

Unexpected exceptions traceback normally rather than being flattened, so bugs stay
reportable.
