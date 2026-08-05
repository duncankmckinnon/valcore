---
name: valcore-cli
description: Reference for every valcore CLI command and flag — version, serve, list, run, export, config, and skills — plus database resolution and exit codes. Use when you need exact valcore command syntax rather than conceptual guidance.
---

# valcore CLI reference

Concepts and workflow live in the `using-valcore` skill. This is syntax.

## Global

```
valcore [--db FILE] [--version] COMMAND [ARGS]...
```

| Option | Meaning |
|---|---|
| `--db FILE` | Override the SQLite database path. |
| `--version` | Print the version and exit. Same string as `valcore version`. |

**Watch the name collision:** at the top level `--version` prints the tool version, but
on `run` and `export` `--version` names an *evaluator version*. They are different
flags at different levels.

### Database resolution

The database path comes from settings unless `--db` overrides it. If a `valcore.db`
exists in the working directory but is **not** the active database, valcore prints a
note on stderr telling you to pass `--db valcore.db`. It does not switch silently.

## Commands

### `valcore version`

Prints the installed version. Falls back to the built-in version file when running from
a source checkout with nothing installed, so it does not crash.

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

`--kind validation` requires every row to carry a label and fails outright if any row
is unlabeled. `--min-accuracy` combined with `--kind validation` is the CI pattern.

### `valcore export EVALUATOR`

Exports an evaluator version as a standalone Python script.

| Option | Meaning |
|---|---|
| `--version TEXT` | Version name. Defaults to the active version. |
| `-o, --output FILE` | Write to a file instead of stdout. |

### `valcore config`

| Subcommand | Purpose |
|---|---|
| `set-key [KEY]` | Store the gateway API key. Prompts hidden if omitted. |
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

Flags are additive and nothing is implicit — `--claude --copilot` writes exactly those
two directories and does not also touch `.agents/`.

| Option | Meaning |
|---|---|
| `--symlink` | Link to the packaged skills so upgrades apply automatically. |
| `--force` | Overwrite differing skills without prompting. |
| `--global` | Use home-level directories instead of repo-level. |

Copy mode skips a skill whose content is already byte-identical, and prompts before
overwriting one you have edited. `--symlink` always replaces.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success. |
| 1 | Domain error. Printed as `error: <message>` on stderr. |
| 2 | `--min-accuracy` threshold not met. |

Unexpected exceptions traceback normally rather than being flattened, so bugs stay
reportable.
