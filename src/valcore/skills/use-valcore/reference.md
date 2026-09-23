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
- [`valcore list`](#valcore-list-evaluatorsdatasetsrunsagentsderivations) — stored resources
- [`valcore run`](#valcore-run-evaluator-evaluator---dataset-dataset) — agent, evaluator, and experiment runs
- [`valcore export`](#valcore-export-evaluator) — standalone judge script or portable eval package
- [`valcore import`](#valcore-import-path) — load a portable eval package back in
- [`valcore config`](#valcore-config) — gateway key and defaults
- [`valcore agent prompt-sync`](#valcore-agent-prompt-sync) — explicit agent template sync
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
| `valcore list <evaluators\|datasets\|runs\|agents\|derivations>` | List resources as a table or, with `--json`, as JSON. |
| `valcore run agent <agent>` | Run an agent over a dataset, one row, ad-hoc inputs, or a literal prompt. |
| `valcore run evaluator <evaluator> --dataset <dataset>` | Run an evaluator version over a dataset. |
| `valcore run experiment <evaluator> --dataset <dataset>` | Run an evaluator through `pydantic_evals.Dataset.evaluate`. |
| `valcore export <evaluator>` | Export an evaluator (and, with `--dataset`, a dataset) as a Python script or, with `--format json`, a portable eval package. |
| `valcore import <file>` | Import a JSON eval package back into the local database. |
| `valcore agent derivation list` | List saved and staged response derivations. |
| `valcore agent derivation save <ref>` | Accept a staged derivation. |
| `valcore agent derivation discard <ref>` | Discard a derivation. |
| `valcore agent prompt-sync status <agent>` | Inspect local agent text and the configured Logfire project's latest variable versions. |
| `valcore agent prompt-sync link <agent> --initial local\|remote` | Link an agent, choosing the initial text source. |
| `valcore agent prompt-sync pull <agent>` | Pull remote text into a new local agent version, with confirmation. |
| `valcore agent prompt-sync push <agent>` | Push local text into new Logfire variable versions, with confirmation. |
| `valcore agent prompt-sync resolve <agent> --choice local\|remote --field FIELD` | Show a three-way diff and explicitly resolve selected conflicts. |
| `valcore agent prompt-sync unlink <agent>` | Remove the local sync link. |
| `valcore agent import <file>` | Import a YAML or JSON AgentSpec and its valcore binding. |
| `valcore agent export <agent>` | Export an agent version as a YAML AgentSpec with its valcore binding. |
| `valcore config set <key> <value>` | Set any config key, including `model`, `local_cli_default`, `port`, `concurrency`, and `db_path`. |
| `valcore config unset <key>` | Remove any config key. |
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

### `valcore list {evaluators|datasets|runs|agents|derivations}`

| Option | Meaning |
|---|---|
| `--json` | Emit JSON instead of a table. |

### `valcore run evaluator EVALUATOR --dataset DATASET`

Runs an evaluator version over a dataset.

| Option | Meaning |
|---|---|
| `--version TEXT` | Version name. Defaults to the evaluator's active version. |
| `--kind [validation\|eval]` | Validate against labels, or score a dataset. |
| `--concurrency INTEGER` | Max concurrent rows. |
| `--json` | Emit JSON results to stdout. |
| `--watch` | Print one line per completed row. |
| `--min-accuracy FLOAT` | Exit 2 below this threshold. |
| `--derivation REF` | Read saved response columns from a derivation. Cannot be used with validation. |

`EVALUATOR` and `DATASET` resolve by exact name first, then by unique id prefix.

`--kind validation` needs a matching label set on the dataset, but not every row need be
labeled under it: rows without a valid label are skipped, and the run scores whatever
subset has ground truth, failing outright only if none do. `--kind validation
--min-accuracy 0.9` is the CI pattern.

### `valcore run experiment EVALUATOR --dataset DATASET`

A second engine over the same data as `run`, driven by `pydantic_evals.Dataset.evaluate`
instead of valcore's own runner. Interchangeable with `run` at the CLI level, except it
has no `--watch` and no cancellation, because `Dataset.evaluate` offers neither.

| Option | Meaning |
|---|---|
| `--version TEXT` | Version name. Defaults to the active version. |
| `--concurrency INTEGER` | Max concurrent rows. |
| `--json` | Emit JSON results to stdout. |

Always runs as `validation` kind; there is no `--kind` here.

### `valcore run agent AGENT`

Use `--dataset DATASET` alone to create a whole-dataset derivation run. It remains staged
until accepted with `--save` or `valcore agent derivation save REF`. Use `--dataset DATASET
--row N` for one existing row, or repeat `--input KEY=VALUE` for an ad-hoc trial; those are
ephemeral unless `--save` is also paired with `--dataset`. `-p/--prompt TEXT` sends literal
text directly to the agent, bypassing its prompt template, and cannot be saved.
An agent may have no input fields or prompt template. In that case ad-hoc `--input input=TEXT`
is sent as plain text, and dataset rows are sent as JSON after the agent's instructions.
Running without any inputs sends an empty request. Dependency mappings remain optional.

### `valcore agent derivation {list|save|discard}`

`list` includes staged entries and marks their state. Saved derivations can also be listed with
`valcore list derivations`; references accept an id prefix or `agent/version/ordinal`.

### `valcore agent prompt-sync`

Syncs only `instructions` and `input_template` with two ordinary Logfire managed
variables in the project selected by the configured write key. The key needs both
`project:read_variables` and `project:write_variables`. Agent runs remain local and
do not contact Logfire for prompt sync.

| Command | Options |
|---|---|
| `status AGENT` | `--json` for the full template status and revision. |
| `link AGENT` | Required `--initial local\|remote`; optional `--json`. |
| `pull AGENT` | Repeat `--field instructions\|input_template` to select fields; `--yes` confirms without prompting; optional `--json`. |
| `push AGENT` | The same field, confirmation, and JSON options as `pull`. |
| `resolve AGENT` | Required `--choice local\|remote` and at least one `--field`; `--yes` skips the confirmation after the choice; optional `--json`. |
| `unlink AGENT` | Removes only the local link; `--yes` skips confirmation; optional `--json`. |

`pull` and `push` preview the affected fields and require confirmation. A conflict
needs `resolve`; it shows the baseline, local text, and remote text before applying
the chosen direction. If the configured key changes, unlink and then link again.

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
| `set KEY VALUE` | Set any config key. Covers every key, including the ones with no named command. |
| `unset KEY` | Remove any config key. Succeeds whether or not it was set. |
| `get [--show-key] [--json]` | Show config. The key is masked unless `--show-key`. |
| `path` | Print the config file path. |
| `edit` | Open the config file in `$EDITOR`. |

[Configuration](#configuration) lists every valid key and the value each accepts; `set`
validates against that before writing, so a bad model string or an unknown local CLI is
refused rather than stored. The one key `set` will not take is `logfire_api_key`, the
legacy combined key -- use `set-logfire-key`, or set the read and write keys separately.
`unset` accepts it along with everything else.

```bash
valcore config set local_cli_default claude
valcore config set model gateway/openai:gpt-5
valcore config set concurrency 16
valcore config unset local_cli_default
```

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

`~/.valcore/config.toml`, or `$VALCORE_HOME/config.toml`. On Windows, `~` is
`$HOME` (normally `C:\Users\<name>`) and access is controlled by filesystem ACLs.
On POSIX systems the file is written with mode `0600`.

These fourteen keys are the complete set. `config set` accepts any of them except
`logfire_api_key`; `config unset` accepts all fourteen. Anything else is refused with an
error naming the valid keys.

| Key | Accepted value | Meaning |
|---|---|---|
| `gateway_api_key` | string | Pydantic AI gateway key, exported as `PYDANTIC_AI_GATEWAY_API_KEY`. |
| `model` | `gateway/<provider>:<model>` or `local/<cli>` | Default model. Validated on write. |
| `local_cli_default` | `claude`, `codex`, or `cursor` | Local CLI used as the default model. Outranks `model`. |
| `port` | integer >= 1 | Default port for `serve`. |
| `concurrency` | integer >= 1 | Default max concurrent rows. |
| `db_path` | path | Default database path. |
| `logfire_token` | string | Write token for tracing valcore's own project. |
| `logfire_read_key` | string | API key for querying traces and hosted datasets in the source-trace project. |
| `logfire_write_key` | string | API key for pushing datasets to the valcore project. |
| `logfire_api_key` | *unset only* | Legacy combined API key; still loaded as both read and write. |
| `logfire_explore_url` | string | Fallback SQL Workbench URL if the read key cannot resolve the project. |
| `logfire_frontend_trace_url` | URL | Trace endpoint generated for a Logfire frontend application. |
| `logfire_frontend_token` | string | Restricted public token generated for a Logfire frontend application. |
| `logfire_session_replay` | boolean | Opt into Early Access browser session replay; defaults to `false`. |

`gateway_api_key`, `logfire_token`, `logfire_api_key`, `logfire_read_key`, and
`logfire_write_key` are secrets: `config set` confirms them as `(hidden)` rather than
echoing the value. `config get` shows the gateway key masked (unless `--show-key`) and the
four Logfire credentials only as present or absent. The restricted public frontend token
is also masked as a precaution.

### Environment variables

| Variable | Effect |
|---|---|
| `PYDANTIC_AI_GATEWAY_API_KEY` | Gateway key. Overrides the stored one when set. |
| `VALCORE_HOME` | Home directory. Defaults to `~/.valcore` (`$HOME\.valcore` on Windows). |
| `VALCORE_DEFAULT_MODEL` | Default model. |
| `VALCORE_DEFAULT_CONCURRENCY` | Default max concurrent rows. |
| `VALCORE_DB_PATH` | Database path. |
| `VALCORE_LOGFIRE_ENABLED` | Enable Logfire instrumentation. |

Precedence, highest first: explicit argument, `VALCORE_*` environment variable,
`config.toml`, built-in default. Within `config.toml`, `local_cli_default` outranks
`model`: a stored local CLI wins over a stored gateway model string.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success. |
| 1 | Domain error. Printed as `error: <message>` on stderr. |
| 2 | `--min-accuracy` threshold not met. |

Unexpected exceptions traceback normally rather than being flattened, so bugs stay
reportable.
