# valcore

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

## Quickstart

Store your gateway API key, then start the app:

```bash
valcore config set-key sk-...
valcore serve
```

`serve` starts the web UI and API on <http://127.0.0.1:8000> and opens a browser
(pass `--no-browser` to skip that, or `--port` to bind elsewhere). Author evaluators
and datasets in the UI, then drive runs from the command line.

The gateway key is stored in `~/.valcore/config.toml` (mode `0600`) and exported to
pydantic-ai as `PYDANTIC_AI_GATEWAY_API_KEY` when a command runs. An already-exported
environment variable always wins over the stored key. Run `valcore config set-key`
with no argument to be prompted without echoing the key.

## Commands

| Command | What it does |
| --- | --- |
| `valcore serve` | Serve the web UI and API (`--port`, `--host`, `--no-browser`). |
| `valcore list <evaluators\|datasets\|runs>` | List resources as a table or, with `--json`, as JSON. |
| `valcore run <evaluator> <dataset>` | Run an evaluator version over a dataset. |
| `valcore export <evaluator>` | Export an evaluator version as a standalone Python script. |
| `valcore config set-key [KEY]` | Store the gateway API key in the config file. |
| `valcore config get` | Show the current config (the key is masked unless `--show-key`). |
| `valcore config path` | Print the path to the config file. |
| `valcore config edit` | Open the config file in `$EDITOR`. |
| `valcore version` | Print the installed valcore version. |

Evaluators, versions, and datasets are addressable by name or by a unique id prefix;
an ambiguous value is an error that lists the candidates. Pass `--db PATH` on the group
to point at a SQLite database other than the default under `~/.valcore`.

`run` accepts `--version` (defaults to the active version), `--kind`, `--concurrency`,
`--watch` (one line per completed row), `--json`, and `--min-accuracy`. Progress goes to
stderr and results go to stdout, so redirecting stdout yields clean JSON. The CLI talks
to SQLite directly, so `run` works whether or not `serve` is up.

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
