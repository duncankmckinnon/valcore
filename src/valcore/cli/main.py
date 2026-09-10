"""The ``valcore`` command: a thin click shell over the existing library.

Every command resolves state through :class:`~valcore.store.Store` directly; the
CLI never talks to the API over HTTP, so ``run`` works whether or not ``serve`` is
up. Domain failures (:class:`~valcore.errors.ValcoreError`) are caught at the
group boundary and printed as ``error: <message>`` to stderr with exit code 1;
unexpected exceptions traceback normally so bugs stay reportable.
"""

import asyncio
import re
import sys
import threading
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

import click

from valcore import config as config_module
from valcore import experiment, logfire_io, logfire_pull, tracing
from valcore.cli.output import emit
from valcore.cli.resolve import resolve_dataset, resolve_evaluator, resolve_version
from valcore.cli.skills import skills
from valcore.config import apply_gateway_key, load_config, save_config, set_key
from valcore.config_io import EvalPackage
from valcore.errors import ConfigError, ContractError, ValcoreError
from valcore.export import render_dataset_module, render_judge_module, render_script
from valcore.models import (
    Annotation,
    Dataset,
    DatasetRow,
    Evaluator,
    EvaluatorVersion,
    LabelSchema,
    LabelSet,
    Run,
    RunKind,
    RunStatus,
    ScoreKind,
    label_set_fields_from_schema,
    validate_version,
)
from valcore.paths import config_path
from valcore.runner import RunEvent, execute_run
from valcore.settings import (
    LOCAL_CLI_NAMES,
    get_settings,
    is_local_cli_model,
    validate_model_string,
)
from valcore.store import Store, create_engine, init_db

_LOCAL_DB = Path("valcore.db")


def _resolve_version() -> str:
    """Report the running version, tolerating a source checkout with no install.

    ``importlib.metadata`` only knows about installed distributions, so running
    straight out of a clone raises. Fall back to the file hatch-vcs bakes in at
    build time, and finally to a placeholder, so ``--version`` never crashes.
    """
    try:
        return package_version("valcore")
    except PackageNotFoundError:
        try:
            from valcore._version import __version__
        except ImportError:
            return "unknown"
        return str(__version__)


class _ValcoreGroup(click.Group):
    """Group that renders domain errors uniformly and exits 1."""

    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except ValcoreError as exc:
            click.echo(f"error: {exc}", err=True)
            ctx.exit(1)


def _store(ctx: click.Context) -> Store:
    """Open (creating tables if needed) the store at the resolved db path."""
    engine = create_engine(ctx.obj["db_path"])
    init_db(engine)
    return Store(engine)


@click.group(cls=_ValcoreGroup)
@click.version_option(_resolve_version(), "--version", message="%(version)s")
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
    tracing.configure_tracing(load_config())

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
    """Print the installed valcore version."""
    click.echo(_resolve_version())


cli.add_command(skills)


# -- serve --------------------------------------------------------------------


@cli.command()
@click.option("--port", type=int, default=None, help="Port to bind (default 8000).")
@click.option("--host", default="127.0.0.1", help="Host to bind.")
@click.option("--no-browser", is_flag=True, help="Do not open a browser.")
def serve(port: int | None, host: str, no_browser: bool) -> None:
    """Serve the valcore web app and API."""
    import uvicorn

    from valcore.api.main import create_app

    resolved_port = port if port is not None else (load_config().port or 8000)
    url = f"http://{host}:{resolved_port}"
    click.echo(f"Serving valcore at {url}", err=True)

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


def _slug(name: str) -> str:
    """Turn an entity name into a filesystem-friendly stem for stdout exports."""
    cleaned = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return cleaned or "package"


def _write_artifact(path: Path, content: str, named: Path | None) -> None:
    """Write one export artifact, refusing to clobber a file the user did not name via ``-o``.

    The ``-o`` path itself may be overwritten — the user chose it — but sibling files a
    multi-file export derives (the split halves, the companion module) must never silently
    replace something already there.
    """
    if path != named and path.exists():
        raise ContractError(
            f"Refusing to overwrite existing file {path}, which was not named via -o."
        )
    path.write_text(content)
    click.echo(f"Wrote {path}", err=True)


@cli.command()
@click.argument("evaluator", required=False)
@click.option("--version", "version_name", default=None, help="Version name (default: active).")
@click.option(
    "-o",
    "--output",
    "output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write output to this file (or its stem's siblings) instead of stdout.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["code", "json"]),
    default="code",
    help="Emit runnable Python (default) or an eval-package JSON document.",
)
@click.option("--dataset", "dataset", default=None, help="Include this dataset in the export.")
@click.option("--split", is_flag=True, help="Write two JSON config files instead of one bundle.")
@click.pass_context
def export(
    ctx: click.Context,
    evaluator: str | None,
    version_name: str | None,
    output: Path | None,
    fmt: str,
    dataset: str | None,
    split: bool,
) -> None:
    """Export an evaluator and/or dataset as Python code or an eval-package JSON document.

    With no new flags this is unchanged: ``export <evaluator>`` renders exactly the standalone
    Python script it always has. ``--format json`` emits the portable eval-package instead, and
    ``--dataset`` folds a dataset into either form.
    """
    if evaluator is None and dataset is None:
        raise ContractError("Nothing to export: name an evaluator or pass --dataset.")
    if split and fmt == "code":
        raise click.UsageError("--split has no meaning for --format code; code has no bundle.")
    if split and output is None:
        raise click.UsageError("--split needs -o to name the two files it writes.")

    store = _store(ctx)
    ev = ver = None
    if evaluator is not None:
        ev = resolve_evaluator(store, evaluator)
        ver = resolve_version(store, ev, version_name)
    ds = rows = label_set = annotations = None
    if dataset is not None:
        ds = resolve_dataset(store, dataset)
        rows = store.list_rows(ds.id)
        label_set, annotations = _primary_ground_truth(store, ds.id, rows)

    if fmt == "code":
        _export_code(ver, ds, rows, output, label_set=label_set, annotations=annotations)
    else:
        _export_json(ev, ver, ds, rows, output, split, label_set=label_set, annotations=annotations)


def _primary_ground_truth(
    store: Store, dataset_id: str, rows: list[DatasetRow]
) -> tuple[LabelSet | None, list[Annotation] | None]:
    """Return (label_set, annotations) for a dataset's primary label set, or (None, None).

    Ground truth now lives on ``LabelSet``/``Annotation``, not on ``DatasetRow``, so export
    must fetch it explicitly rather than reading it off the row -- mirroring the identical
    helper in ``routes/datasets.py``.
    """
    label_set = store.primary_label_set(dataset_id)
    if label_set is None:
        return None, None
    annotations = store.list_annotations_for_rows(label_set.id, [row.id for row in rows])
    return label_set, annotations


def _export_code(
    ver: EvaluatorVersion | None,
    ds: Dataset | None,
    rows: list[DatasetRow] | None,
    output: Path | None,
    *,
    label_set: LabelSet | None = None,
    annotations: list[Annotation] | None = None,
) -> None:
    """Emit the Python-code form: a standalone script and/or a dataset module."""
    if ver is not None and ds is not None:
        if output is None:
            raise click.UsageError("A script and a dataset module cannot share stdout; pass -o.")
        _write_artifact(output, render_script(ver), output)
        _write_artifact(
            output.parent / f"{output.stem}.dataset.py",
            render_dataset_module(ds, rows, label_set=label_set, annotations=annotations),
            output,
        )
        return

    content = (
        render_script(ver)
        if ver is not None
        else render_dataset_module(ds, rows, label_set=label_set, annotations=annotations)
    )
    if output is None:
        click.echo(content, nl=False)
    else:
        _write_artifact(output, content, output)


def _export_json(
    ev: Evaluator | None,
    ver: EvaluatorVersion | None,
    ds: Dataset | None,
    rows: list[DatasetRow] | None,
    output: Path | None,
    split: bool,
    *,
    label_set: LabelSet | None = None,
    annotations: list[Annotation] | None = None,
) -> None:
    """Emit the JSON eval-package form, writing the companion judge module beside a config file."""
    pkg = None
    if ver is not None:
        pkg = EvalPackage.from_version(ver)
    if ds is not None:
        ds_pkg = EvalPackage.from_dataset(ds, rows, label_set=label_set, annotations=annotations)
        pkg = ds_pkg if pkg is None else pkg.merge(ds_pkg)

    mode = "split" if split else "bundled"
    # Slugify the evaluator (not version) name, or the dataset name, for a stdout stem.
    stem = output.stem if output is not None else _slug(ev.name if ev is not None else ds.name)
    files = pkg.to_text(stem, mode)

    if output is None:
        # Bundled JSON is a single document, so stdout can hold it. The companion judge module
        # has nowhere to go without -o; note its omission on stderr and keep stdout pure JSON.
        click.echo(next(iter(files.values())), nl=False)
        if ver is not None:
            click.echo("note: companion module valcore_judge.py omitted (no -o).", err=True)
        return

    for filename, content in files.items():
        _write_artifact(output.parent / filename, content, output)
    if ver is not None:
        package_filename = f"{stem}.agent.json" if split else f"{stem}.json"
        _write_artifact(
            output.parent / "valcore_judge.py", render_judge_module(ver, package_filename), output
        )


@cli.command(name="import")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--name", "name", default=None, help="Override the imported dataset's name.")
@click.pass_context
def import_(ctx: click.Context, path: Path, name: str | None) -> None:
    """Import an eval-package JSON document, creating its dataset and/or evaluator."""
    if path.suffix == ".py":
        raise ContractError("Cannot import a Python script; the importable package format is JSON.")

    store = _store(ctx)
    pkg = EvalPackage.from_text(path.read_text())

    # Validate the reconstructed version up front, before any store write, so a package whose
    # agent fails validation persists nothing at all — not even its dataset half.
    version_fields = None
    if pkg.spec is not None:
        version_fields = pkg.to_version_fields()
        # ``to_version_fields`` carries ``score_kind`` as its raw string; a table-backed
        # ``EvaluatorVersion`` does not coerce it on construction, so validation's enum identity
        # check would misread a categorical field as numeric. Coerce once, for validate and store.
        version_fields["score_kind"] = ScoreKind(version_fields["score_kind"])
        validate_version(EvaluatorVersion(evaluator_id="", **version_fields))

    if pkg.dataset is not None:
        ds_name, columns, label_schema, prepared_rows, row_annotations = pkg.to_dataset_fields()
        # A bare case array (Logfire's export shape) carries no dataset name, so fall back to the
        # filename the way the evaluator branch below already does.
        ds_name = name or ds_name or path.stem
        created = store.create_dataset(ds_name, "", columns)
        rows = store.add_prepared_rows(created.id, prepared_rows)
        if label_schema:
            label_set = store.create_label_set(
                created.id,
                name="Imported labels",
                description="",
                **label_set_fields_from_schema(LabelSchema.model_validate(label_schema)),
            )
            for row, fields in zip(rows, row_annotations):
                if fields is not None:
                    store.set_annotation(label_set.id, row.id, **fields)
        click.echo(f"dataset {created.id} {created.name}")

    if version_fields is not None:
        ev_name = pkg.spec.name or path.stem
        evaluator = store.create_evaluator(ev_name)
        # create_version already makes the new version the evaluator's active one (store.py).
        version = store.create_version(evaluator.id, **version_fields)
        click.echo(f"evaluator {evaluator.id} {evaluator.name} (version {version.id})")


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
    """Run an evaluator version over a dataset.

    Resolving the version first lets the gateway-key guard stand down for a
    ``local/<cli>`` model, which reaches an already-logged-in CLI on this machine
    rather than the gateway. Resolution only reads, so nothing is created ahead of the guard.
    """
    store = _store(ctx)
    ev = resolve_evaluator(store, evaluator)
    ver = resolve_version(store, ev, version_name)
    if not is_local_cli_model(ver.model):
        config_module.require_gateway_key()
    ds = resolve_dataset(store, dataset)

    workers = concurrency if concurrency is not None else get_settings().default_concurrency
    created = store.create_run(RunKind(kind), ver.id, ds.id, workers)

    finished = asyncio.run(_drive_run(store, created.id, watch))
    if finished.status is RunStatus.FAILED:
        raise ValcoreError(finished.error or "Run failed.")

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


# -- experiment -----------------------------------------------------------------


async def _drive_experiment(store: Store, run_id: str) -> Run:
    """Drive ``experiment.execute_experiment``, streaming progress to stderr as it goes.

    Mirrors ``_drive_run``'s non-``--watch`` progress line; there is no ``--watch`` mode
    here because ``Dataset.evaluate`` has no cancellation to make one meaningful.
    """
    state = {"done": 0, "total": 0}

    async def on_event(event: RunEvent) -> None:
        if event.type == "started":
            state["total"] = event.payload["total"]
        elif event.type == "row":
            state["done"] += 1
            click.echo(f"\r{state['done']}/{state['total']}", nl=False, err=True)

    run = await experiment.execute_experiment(store, run_id, on_event=on_event)
    if state["total"]:
        click.echo("", err=True)
    return run


@cli.command(name="experiment")
@click.argument("evaluator")
@click.argument("dataset")
@click.option("--version", "version_name", default=None, help="Version name (default: active).")
@click.option("--concurrency", type=int, default=None, help="Max concurrent rows.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON results to stdout.")
@click.pass_context
def experiment_cmd(
    ctx: click.Context,
    evaluator: str,
    dataset: str,
    version_name: str | None,
    concurrency: int | None,
    as_json: bool,
) -> None:
    """Run an evaluator version over a dataset via ``pydantic_evals.Dataset.evaluate``.

    A second engine over the same data as ``run``, interchangeable with it at the CLI
    level. It has no ``--watch`` and no cancellation, because ``Dataset.evaluate`` offers
    neither.

    Resolving the version first lets the gateway-key guard stand down for a
    ``local/<cli>`` model, which reaches an already-logged-in CLI on this machine
    rather than the gateway. Resolution only reads, so nothing is created ahead of the guard.
    """
    store = _store(ctx)
    ev = resolve_evaluator(store, evaluator)
    ver = resolve_version(store, ev, version_name)
    if not is_local_cli_model(ver.model):
        config_module.require_gateway_key()
    ds = resolve_dataset(store, dataset)

    workers = concurrency if concurrency is not None else get_settings().default_concurrency
    created = store.create_run(RunKind.VALIDATION, ver.id, ds.id, workers)

    finished = asyncio.run(_drive_experiment(store, created.id))
    if finished.status is RunStatus.FAILED:
        raise ValcoreError(finished.error or "Experiment failed.")

    _emit_run(store, finished, as_json)


# -- config -------------------------------------------------------------------


@cli.group()
def config() -> None:
    """Read and write the valcore config file."""


@config.command("set-key")
@click.argument("key", required=False)
def config_set_key(key: str | None) -> None:
    """Store the gateway API key in the config file."""
    if key is None:
        key = click.prompt("Gateway API key", hide_input=True)
    set_key(key)
    click.echo(f"Saved gateway API key to {config_path()}", err=True)


def _parse_local_cli_default(value: str) -> str:
    """Return `value` if it names a supported local CLI, else raise ConfigError."""
    if value not in LOCAL_CLI_NAMES:
        raise ConfigError(
            f"{value!r} is not a known local CLI; valid names are {sorted(LOCAL_CLI_NAMES)}."
        )
    return value


def _parse_positive_int(field: str) -> Callable[[str], int]:
    """Return a parser turning a string into a positive int for `field`."""

    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError:
            raise ConfigError(f"{field} must be a whole number, not {value!r}.") from None
        if number < 1:
            raise ConfigError(f"{field} must be at least 1, not {number}.")
        return number

    return parse


def _parse_model(value: str) -> str:
    """Return `value` if it is a valid model string; validate_model_string raises otherwise."""
    validate_model_string(value)
    return value


@dataclass(frozen=True)
class _ConfigField:
    """One settable config.toml key: how to parse its value, and whether it is a secret.

    ``settable`` is False for ``logfire_api_key``: it is the legacy combined key that
    ``set_logfire_api_key`` migrates away from by writing the split read/write fields, so
    ``set`` would reintroduce what the migration removes. ``unset`` still clears it, which
    is the only thing a user with a legacy config actually wants to do to it.
    """

    parse: Callable[[str], object] = str
    secret: bool = False
    settable: bool = True


_CONFIG_FIELDS: dict[str, _ConfigField] = {
    "gateway_api_key": _ConfigField(secret=True),
    "model": _ConfigField(parse=_parse_model),
    "local_cli_default": _ConfigField(parse=_parse_local_cli_default),
    "port": _ConfigField(parse=_parse_positive_int("port")),
    "concurrency": _ConfigField(parse=_parse_positive_int("concurrency")),
    "db_path": _ConfigField(parse=Path),
    "logfire_token": _ConfigField(secret=True),
    "logfire_api_key": _ConfigField(secret=True, settable=False),
    "logfire_read_key": _ConfigField(secret=True),
    "logfire_write_key": _ConfigField(secret=True),
    "logfire_explore_url": _ConfigField(),
}


def _config_field(key: str, *, for_set: bool) -> _ConfigField:
    """Look up `key`, raising ConfigError naming the valid alternatives when it is unknown."""
    field = _CONFIG_FIELDS.get(key)
    if field is None:
        raise ConfigError(f"Unknown config key {key!r}; valid keys are {sorted(_CONFIG_FIELDS)}.")
    if for_set and not field.settable:
        raise ConfigError(
            f"{key!r} is the legacy combined Logfire key and cannot be set directly. Use "
            "'valcore config set-logfire-key' to store one key as both, or set "
            "logfire_read_key and logfire_write_key separately."
        )
    return field


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Set any config.toml KEY to VALUE, leaving every other key untouched."""
    field = _config_field(key, for_set=True)
    cfg = load_config()
    setattr(cfg, key, field.parse(value))
    save_config(cfg)
    shown = "(hidden)" if field.secret else value
    click.echo(f"Set {key} = {shown} in {config_path()}", err=True)


@config.command("unset")
@click.argument("key")
def config_unset(key: str) -> None:
    """Remove any config.toml KEY, leaving every other key untouched.

    Unsetting a key that is already absent succeeds: the requested end state holds either
    way, and a CLI that errors on it cannot be used to make config idempotent.
    """
    _config_field(key, for_set=False)
    cfg = load_config()
    setattr(cfg, key, None)
    save_config(cfg)
    click.echo(f"Unset {key} in {config_path()}", err=True)


@config.command("get")
@click.option("--show-key", is_flag=True, help="Reveal the full gateway API key.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of a table.")
def config_get(show_key: bool, as_json: bool) -> None:
    """Show the current config, masking the gateway key by default.

    The Logfire token and API keys are never revealed, even with ``--show-key``: that flag
    already governs revealing the gateway key specifically, and does not newly govern these.
    Only their presence is shown.
    """
    cfg = load_config()
    data = cfg.model_dump(mode="json")
    if config_module.gateway_key_present(cfg) and not show_key:
        data["gateway_api_key"] = f"sk-…{cfg.gateway_api_key[-4:]}" if cfg.gateway_api_key else True
    data["logfire_token"] = config_module.logfire_token_present(cfg)
    data["logfire_api_key"] = config_module.logfire_api_key_present(cfg)
    data["logfire_read_key"] = config_module.logfire_read_key_present(cfg)
    data["logfire_write_key"] = config_module.logfire_write_key_present(cfg)
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


@config.command("set-logfire-token")
@click.argument("token", required=False)
def config_set_logfire_token(token: str | None) -> None:
    """Store the Logfire write token (for tracing) in the config file."""
    if token is None:
        token = click.prompt("Logfire token", hide_input=True)
    config_module.set_logfire_token(token)
    click.echo(f"Saved Logfire token to {config_path()}", err=True)


@config.command("set-logfire-key")
@click.argument("key", required=False)
def config_set_logfire_key(key: str | None) -> None:
    """Store one Logfire API key as both the read and write keys."""
    if key is None:
        key = click.prompt("Logfire API key", hide_input=True)
    config_module.set_logfire_api_key(key)
    click.echo(f"Saved Logfire API key to {config_path()}", err=True)


@config.command("set-logfire-read-key")
@click.argument("key", required=False)
def config_set_logfire_read_key(key: str | None) -> None:
    """Store the Logfire read key (query traces and hosted datasets in the source project)."""
    if key is None:
        key = click.prompt("Logfire read key", hide_input=True)
    config_module.set_logfire_read_key(key)
    click.echo(f"Saved Logfire read key to {config_path()}", err=True)


@config.command("set-logfire-write-key")
@click.argument("key", required=False)
def config_set_logfire_write_key(key: str | None) -> None:
    """Store the Logfire write key (push datasets to the valcore project)."""
    if key is None:
        key = click.prompt("Logfire write key", hide_input=True)
    config_module.set_logfire_write_key(key)
    click.echo(f"Saved Logfire write key to {config_path()}", err=True)


@config.command("set-logfire-explore-url")
@click.argument("url", required=False)
def config_set_logfire_explore_url(url: str | None) -> None:
    """Store a fallback Logfire SQL Workbench URL if the read key cannot resolve the project."""
    if url is None:
        url = click.prompt("Logfire SQL Workbench URL")
    config_module.set_logfire_explore_url(url)
    click.echo(f"Saved Logfire SQL Workbench URL to {config_path()}", err=True)


# -- logfire --------------------------------------------------------------------


@cli.group(name="logfire")
def logfire_group() -> None:
    """Pull datasets from Logfire queries or hosted datasets, or push them to the hosted store."""


@logfire_group.command("push")
@click.argument("dataset")
@click.option(
    "--name", "name", default=None, help="Name for the pushed dataset (default: its own)."
)
@click.option(
    "--description", "description", default=None, help="Description for the pushed dataset."
)
@click.option(
    "--on-conflict",
    "on_conflict",
    type=click.Choice(["update", "error"]),
    default="update",
    help="How to handle a case ID that already exists on the hosted dataset.",
)
@click.pass_context
def logfire_push(
    ctx: click.Context,
    dataset: str,
    name: str | None,
    description: str | None,
    on_conflict: str,
) -> None:
    """Push a dataset to Logfire's hosted dataset store."""
    store = _store(ctx)
    ds = resolve_dataset(store, dataset)
    rows = store.list_rows(ds.id)

    result = asyncio.run(
        logfire_io.push_dataset(
            ds, rows, name=name, description=description, on_conflict=on_conflict
        )
    )
    emit(result, as_json=False, columns=["id", "name", "case_count"])


@logfire_group.command("pull")
@click.option("--sql", "sql_text", default=None, help="SQL to run against Logfire.")
@click.option(
    "--sql-file",
    "sql_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Read SQL from a file instead of --sql.",
)
@click.option("--name", required=True, help="Name for the created dataset.")
@click.option("--description", default="", help="Description stored on the dataset.")
@click.option(
    "--count",
    "sample_n",
    required=True,
    type=int,
    help="Number of top-level entries to sample.",
)
@click.option("--seed", type=int, default=None, help="RNG seed; generated and stored when omitted.")
@click.option("--min-timestamp", default=None, help="ISO-8601 lower bound (default: 24 hours ago).")
@click.option("--max-timestamp", default=None, help="ISO-8601 upper bound.")
@click.option("--label-column", default=None, help="SQL column to lift into the dataset label.")
@click.option(
    "--label-schema",
    "label_schema_json",
    default=None,
    help="JSON label schema; required when --label-column is set.",
)
@click.pass_context
def logfire_pull_cmd(
    ctx: click.Context,
    sql_text: str | None,
    sql_file: Path | None,
    name: str,
    description: str,
    sample_n: int,
    seed: int | None,
    min_timestamp: str | None,
    max_timestamp: str | None,
    label_column: str | None,
    label_schema_json: str | None,
) -> None:
    """Create a local dataset from a Logfire SQL query."""
    if (sql_text is None) == (sql_file is None):
        raise click.UsageError("Provide exactly one of --sql or --sql-file.")
    sql = (sql_file.read_text() if sql_file is not None else sql_text) or ""
    schema = None
    if label_schema_json is not None:
        schema = LabelSchema.model_validate_json(label_schema_json).model_dump(mode="json")
    if label_column is not None and schema is None:
        raise click.UsageError("--label-column requires --label-schema.")

    def parse_ts(value: str | None) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    result = asyncio.run(
        logfire_pull.pull_records(
            sql,
            sample_n,
            seed=seed,
            min_timestamp=parse_ts(min_timestamp),
            max_timestamp=parse_ts(max_timestamp),
            label_column=label_column,
        )
    )
    store = _store(ctx)
    dataset = store.create_dataset(
        name=name,
        description=description,
        columns=result.columns,
    )
    rows = store.add_prepared_rows(dataset.id, result.prepared)
    if schema:
        label_set = store.create_label_set(
            dataset.id,
            name="Imported labels",
            description="",
            **label_set_fields_from_schema(LabelSchema.model_validate(schema)),
        )
        for row, fields in zip(rows, result.row_annotations):
            if fields is not None:
                store.set_annotation(label_set.id, row.id, **fields)
    store.set_logfire_pull(
        dataset.id,
        sql=result.sql,
        sample_n=result.sample_n,
        seed=result.seed,
        min_timestamp=result.min_timestamp,
        max_timestamp=result.max_timestamp,
        label_column=result.label_column,
    )
    emit(
        {"id": dataset.id, "name": dataset.name, "row_count": len(rows), "seed": result.seed},
        as_json=False,
        columns=["id", "name", "row_count", "seed"],
    )


@logfire_group.command("list")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of a table.")
def logfire_list(as_json: bool) -> None:
    """List hosted datasets in the Logfire project the read key is scoped to."""
    rows = asyncio.run(logfire_io.list_hosted_datasets())
    emit(rows, as_json, columns=["name", "case_count", "description"])


@logfire_group.command("fetch")
@click.argument("source_name")
@click.option(
    "--name",
    default=None,
    help="Name for the local dataset (default: the hosted dataset's name).",
)
@click.option("--description", default="", help="Description stored on the local dataset.")
@click.pass_context
def logfire_fetch(ctx: click.Context, source_name: str, name: str | None, description: str) -> None:
    """Create a local dataset from a hosted Logfire dataset."""
    result = asyncio.run(logfire_io.fetch_hosted_dataset(source_name))
    store = _store(ctx)
    dataset = store.create_dataset(
        name=(name or "").strip() or result.name,
        description=description,
        columns=result.columns,
    )
    rows = store.add_prepared_rows(dataset.id, result.prepared)
    if result.label_schema:
        label_set = store.create_label_set(
            dataset.id,
            name="Imported labels",
            description="",
            **label_set_fields_from_schema(LabelSchema.model_validate(result.label_schema)),
        )
        for row, fields in zip(rows, result.row_annotations):
            if fields is not None:
                store.set_annotation(label_set.id, row.id, **fields)
    emit(
        {"id": dataset.id, "name": dataset.name, "row_count": len(rows)},
        as_json=False,
        columns=["id", "name", "row_count"],
    )


def main() -> None:
    """Console-script entry point."""
    cli()


if __name__ == "__main__":
    main()
