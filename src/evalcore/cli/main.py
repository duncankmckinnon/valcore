"""The ``eval-core`` command: a thin click shell over the existing library.

Every command resolves state through :class:`~evalcore.store.Store` directly; the
CLI never talks to the API over HTTP, so ``run`` works whether or not ``serve`` is
up. Domain failures (:class:`~evalcore.errors.EvalCoreError`) are caught at the
group boundary and printed as ``error: <message>`` to stderr with exit code 1;
unexpected exceptions traceback normally so bugs stay reportable.
"""

import asyncio
import sys
import threading
import webbrowser
from importlib.metadata import version as package_version
from pathlib import Path

import click

from evalcore.cli.output import emit
from evalcore.cli.resolve import resolve_dataset, resolve_evaluator, resolve_version
from evalcore.config import apply_gateway_key, load_config, save_config, set_key
from evalcore.errors import ContractError, EvalCoreError
from evalcore.export import render_script
from evalcore.models import Run, RunKind, RunStatus
from evalcore.paths import config_path
from evalcore.runner import RunEvent, execute_run
from evalcore.settings import get_settings
from evalcore.store import Store, create_engine, init_db

_LOCAL_DB = Path("evalcore.db")


class _EvalCoreGroup(click.Group):
    """Group that renders domain errors uniformly and exits 1."""

    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except EvalCoreError as exc:
            click.echo(f"error: {exc}", err=True)
            ctx.exit(1)


def _store(ctx: click.Context) -> Store:
    """Open (creating tables if needed) the store at the resolved db path."""
    engine = create_engine(ctx.obj["db_path"])
    init_db(engine)
    return Store(engine)


@click.group(cls=_EvalCoreGroup)
@click.option(
    "--db",
    "db",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Override the SQLite database path.",
)
@click.pass_context
def cli(ctx: click.Context, db: Path | None) -> None:
    """Develop, run, and export agentic evaluations from the command line."""
    apply_gateway_key(load_config())

    db_path = db if db is not None else get_settings().db_path
    if _LOCAL_DB.exists() and not Path(db_path).exists():
        click.echo(
            f"note: {_LOCAL_DB} exists in this directory but is not the active database; "
            f"pass --db {_LOCAL_DB} to use it.",
            err=True,
        )
    ctx.obj = {"db_path": db_path}


# -- version ------------------------------------------------------------------


@cli.command()
def version() -> None:
    """Print the installed eval-core version."""
    click.echo(package_version("eval-core"))


# -- serve --------------------------------------------------------------------


@cli.command()
@click.option("--port", type=int, default=None, help="Port to bind (default 8000).")
@click.option("--host", default="127.0.0.1", help="Host to bind.")
@click.option("--no-browser", is_flag=True, help="Do not open a browser.")
def serve(port: int | None, host: str, no_browser: bool) -> None:
    """Serve the eval-core web app and API."""
    import uvicorn

    from evalcore.api.main import create_app

    resolved_port = port if port is not None else (load_config().port or 8000)
    url = f"http://{host}:{resolved_port}"
    click.echo(f"Serving eval-core at {url}", err=True)

    if not no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run(create_app(), host=host, port=resolved_port)


# -- list ---------------------------------------------------------------------


@cli.command(name="list")
@click.argument("kind", type=click.Choice(["evaluators", "datasets", "runs"]))
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of a table.")
@click.pass_context
def list_(ctx: click.Context, kind: str, as_json: bool) -> None:
    """List evaluators, datasets, or runs."""
    store = _store(ctx)
    if kind == "evaluators":
        rows = [e.model_dump() for e in store.list_evaluators()]
        emit(rows, as_json, columns=["id", "name", "active_version_id", "description"])
    elif kind == "datasets":
        rows = [d.model_dump() for d in store.list_datasets()]
        emit(rows, as_json, columns=["id", "name", "description", "columns"])
    else:
        rows = []
        for run in store.list_runs():
            row = run.model_dump()
            row["accuracy"] = run.metrics.get("accuracy") if run.metrics else None
            rows.append(row)
        emit(
            rows, as_json, columns=["id", "kind", "status", "version_id", "dataset_id", "accuracy"]
        )


# -- export -------------------------------------------------------------------


@cli.command()
@click.argument("evaluator")
@click.option("--version", "version_name", default=None, help="Version name (default: active).")
@click.option(
    "-o",
    "--output",
    "output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the script to this file instead of stdout.",
)
@click.pass_context
def export(
    ctx: click.Context, evaluator: str, version_name: str | None, output: Path | None
) -> None:
    """Export an evaluator version as a standalone Python script."""
    store = _store(ctx)
    ev = resolve_evaluator(store, evaluator)
    ver = resolve_version(store, ev, version_name)
    script = render_script(ver)
    if output is not None:
        output.write_text(script)
        click.echo(f"Wrote {output}", err=True)
    else:
        click.echo(script, nl=False)


# -- run ----------------------------------------------------------------------


def _emit_run(store: Store, run: Run, as_json: bool) -> None:
    """Write a finished run's outcome to stdout, as JSON or a summary table."""
    results = store.list_results(run.id)
    if as_json:
        payload = {
            "run_id": run.id,
            "kind": run.kind.value,
            "status": run.status.value,
            "metrics": run.metrics,
            "results": [r.model_dump() for r in results],
        }
        emit(payload, as_json=True)
        return

    click.echo(f"run {run.id} {run.status.value}")
    if run.metrics is not None:
        emit(run.metrics, as_json=False, columns=list(run.metrics.keys()))


async def _drive_run(store: Store, run_id: str, watch: bool) -> Run:
    """Drive ``execute_run``, streaming progress to stderr as it goes."""
    state = {"done": 0, "total": 0}

    async def on_event(event: RunEvent) -> None:
        if event.type == "started":
            state["total"] = event.payload["total"]
        elif event.type == "row":
            state["done"] += 1
            if watch:
                status = "ok" if event.payload["success"] else "error"
                click.echo(
                    f"[{state['done']}/{state['total']}] {event.payload['row_id']} {status} "
                    f"score={event.payload.get('score_value')}",
                    err=True,
                )
            else:
                click.echo(f"\r{state['done']}/{state['total']}", nl=False, err=True)

    run = await execute_run(store, run_id, on_event=on_event)
    if not watch and state["total"]:
        click.echo("", err=True)
    return run


@cli.command()
@click.argument("evaluator")
@click.argument("dataset")
@click.option("--version", "version_name", default=None, help="Version name (default: active).")
@click.option(
    "--kind",
    type=click.Choice([k.value for k in RunKind]),
    default=RunKind.VALIDATION.value,
    help="Whether to validate against labels or score a dataset.",
)
@click.option("--concurrency", type=int, default=None, help="Max concurrent rows.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON results to stdout.")
@click.option("--watch", is_flag=True, help="Print one line per completed row.")
@click.option("--min-accuracy", type=float, default=None, help="Fail with exit 2 below this.")
@click.pass_context
def run(
    ctx: click.Context,
    evaluator: str,
    dataset: str,
    version_name: str | None,
    kind: str,
    concurrency: int | None,
    as_json: bool,
    watch: bool,
    min_accuracy: float | None,
) -> None:
    """Run an evaluator version over a dataset."""
    store = _store(ctx)
    ev = resolve_evaluator(store, evaluator)
    ver = resolve_version(store, ev, version_name)
    ds = resolve_dataset(store, dataset)

    workers = concurrency if concurrency is not None else get_settings().default_concurrency
    created = store.create_run(RunKind(kind), ver.id, ds.id, workers)

    finished = asyncio.run(_drive_run(store, created.id, watch))
    if finished.status is RunStatus.FAILED:
        raise EvalCoreError(finished.error or "Run failed.")

    _emit_run(store, finished, as_json)

    if min_accuracy is not None:
        accuracy = (finished.metrics or {}).get("accuracy")
        if accuracy is None:
            raise ContractError(
                "--min-accuracy needs a categorical accuracy metric, which this run did "
                "not produce (numeric or unlabeled runs have no accuracy)."
            )
        if accuracy < min_accuracy:
            click.echo(
                f"accuracy {accuracy:.4f} is below the required {min_accuracy}",
                err=True,
            )
            sys.exit(2)


# -- config -------------------------------------------------------------------


@cli.group()
def config() -> None:
    """Read and write the eval-core config file."""


@config.command("set-key")
@click.argument("key", required=False)
def config_set_key(key: str | None) -> None:
    """Store the gateway API key in the config file."""
    if key is None:
        key = click.prompt("Gateway API key", hide_input=True)
    set_key(key)
    click.echo(f"Saved gateway API key to {config_path()}", err=True)


@config.command("get")
@click.option("--show-key", is_flag=True, help="Reveal the full gateway API key.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of a table.")
def config_get(show_key: bool, as_json: bool) -> None:
    """Show the current config, masking the gateway key by default."""
    cfg = load_config()
    data = cfg.model_dump(mode="json")
    if cfg.gateway_api_key and not show_key:
        data["gateway_api_key"] = f"sk-…{cfg.gateway_api_key[-4:]}"
    emit(data, as_json, columns=list(data.keys()))


@config.command("path")
def config_path_cmd() -> None:
    """Print the path to the config file."""
    click.echo(config_path())


@config.command("edit")
def config_edit() -> None:
    """Open the config file in $EDITOR."""
    path = config_path()
    if not path.exists():
        save_config(load_config())
    click.edit(filename=str(path))


def main() -> None:
    """Console-script entry point."""
    cli()


if __name__ == "__main__":
    main()
