<p align="center">
  <img
    src="https://raw.githubusercontent.com/duncankmckinnon/valcore/main/docs/img/logo.png"
    alt="valcore"
    width="420"
  >
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
