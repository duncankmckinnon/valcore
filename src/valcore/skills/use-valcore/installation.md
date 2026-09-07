# Installing and configuring valcore for headless use

For an agent driving valcore non-interactively — no browser, no human at a setup
wizard: what to check before running anything, and what a missing piece actually looks
like when you hit it. [reference.md](reference.md) has exact command syntax;
[SKILL.md](SKILL.md) has the workflow. This is the "is my environment actually ready"
checklist.

## Required vs optional

Nothing here is required just to author or edit evaluators and datasets by hand. Two
kinds of key gate specific capabilities:

| Capability | Needs |
|---|---|
| `run` / `experiment` / seeded generation, on a `gateway/...` model | gateway API key |
| `run` / `experiment`, on a `local/<cli>` model | the named CLI installed and already logged in on this machine — no gateway key |
| `logfire pull` / `list` / `fetch` | Logfire read key (or the legacy combined key) |
| `logfire push` | Logfire write key (or the legacy combined key) |
| Tracing valcore's own runs | Logfire token — optional; silently does nothing without it |

Check what is actually configured before relying on it:

```bash
valcore config get --json
```

Each key reports `true`/masked or absent — never the raw value. A missing gateway key
does not fail at startup; it fails the first time something needs it, as
`error: No gateway API key configured. Run 'valcore config set-key' or export
PYDANTIC_AI_GATEWAY_API_KEY.`

## Local CLI routes: route name vs binary

An evaluator version's `model` can be `local/claude`, `local/codex`, or `local/cursor`
instead of a `gateway/...` string, reaching an already-logged-in CLI on this machine
instead of the gateway. The route name is **not** the binary valcore actually shells
out to:

| Route name | Binary on `PATH` | Confirm it works |
|---|---|---|
| `local/claude` | `claude` | `claude --version` |
| `local/codex` | `codex` | `codex --version` |
| `local/cursor` | `cursor-agent` | `cursor-agent --version` |

valcore does not check any of this before running — there is no preflight, and it
never triggers a login flow. A missing binary or an expired login surfaces as a raw
error from deep inside the run (a subprocess `FileNotFoundError`, or the adapter's own
`RuntimeError: <cli> exited with code ...` carrying whatever the CLI printed), not a
clean message up front. If a `local/<cli>` run fails, run the version check above
before assuming valcore itself is broken.

Each adapter also runs its CLI in the tightest non-interactive, read-only mode that
CLI offers (no file edits, no shell, no network where the CLI can disable them) —
see `src/valcore/local_cli/*_adapter.py` for the exact invocation if you need it.

## End-to-end checklist

1. `valcore version` — confirms the install itself works.
2. `valcore config get --json` — confirms which keys are set.
3. For any `local/<cli>` model in play: `<binary> --version` succeeds (see the table
   above for which binary backs which route name).
4. `valcore list evaluators --json` and `valcore list datasets --json` — confirms the
   database is reachable and has the expected content.
5. `valcore run <evaluator> <dataset> --kind validation --min-accuracy 0` — a cheap
   dry run that exercises the whole path (model resolution, the gateway or local CLI
   call, scoring) without asserting a real threshold.
