# valcore CLI reference

Syntax only. Concepts, workflow, and gateway setup are in [SKILL.md](SKILL.md).

## Contents

- [Global](#global) — `--db`, `--version`, database resolution
- [`valcore version`](#valcore-version)
- [`valcore serve`](#valcore-serve) — run the web app and API
- [`valcore list`](#valcore-list-evaluatorsdatasetsruns) — evaluators, datasets, runs
- [`valcore run`](#valcore-run-evaluator-dataset) — validation and eval runs
- [`valcore export`](#valcore-export-evaluator) — standalone judge script
- [`valcore config`](#valcore-config) — gateway key and defaults
- [`valcore skills`](#valcore-skills) — install these skills into agent directories
- [`valcore logfire`](#valcore-logfire) — pull from a query or hosted dataset; push to hosted store
- [Not in the CLI](#not-in-the-cli) — seeded generation is API and web only
- [Configuration](#configuration) — `config.toml` keys, `VALCORE_*` environment variables
- [Exit codes](#exit-codes)

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

### `valcore export EVALUATOR`

Exports an evaluator version as a standalone Python script.

| Option | Meaning |
|---|---|
| `--version TEXT` | Version name. Defaults to the active version. |
| `-o, --output FILE` | Write to a file instead of stdout. |

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

`~/.valcore/config.toml`, or `$VALCORE_HOME/config.toml`. Written with mode `0600`.

These eleven keys are the complete set. `config set` accepts any of them except
`logfire_api_key`; `config unset` accepts all eleven. Anything else is refused with an
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

`gateway_api_key`, `logfire_token`, `logfire_api_key`, `logfire_read_key`, and
`logfire_write_key` are secrets: `config set` confirms them as `(hidden)` rather than
echoing the value. `config get` shows the gateway key masked (unless `--show-key`) and the
four Logfire credentials only as present or absent.

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
