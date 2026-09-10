"""Dataset CRUD, file upload, generation, and labeling endpoints."""

import csv
import io
import json
import re
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, ConfigDict

from valcore import config
from valcore.api.deps import get_store
from valcore.config_io import EvalPackage
from valcore.datagen import generate_rows
from valcore.errors import ContractError
from valcore.export import render_dataset_module, render_judge_module
from valcore.logfire_io import fetch_hosted_dataset, list_hosted_datasets, push_dataset
from valcore.logfire_pull import pull_records
from valcore.models import (
    LabelSchema,
    LabelSource,
    ScoreKind,
    annotation_ground_truth,
    label_schema_from_label_set,
    label_set_fields_from_schema,
)
from valcore.seeding import dataset_shape_from_version
from valcore.settings import get_settings, is_local_cli_model
from valcore.store import Store

router = APIRouter(prefix="/api/datasets", tags=["datasets"])

StoreDep = Annotated[Store, Depends(get_store)]

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_MAX_GENERATE_COUNT = 200
_JSONL_INFER_LIMIT = 50


def _require_gateway_key_unless_local() -> None:
    """Require the gateway key unless generation resolves to a local CLI model.

    None of the generate routes pass an explicit model, so they all resolve to
    ``get_settings().default_model`` inside ``generate_rows``. A ``local/<cli>``
    default reaches an already-logged-in CLI on this machine, never the gateway, so
    demanding a gateway key there would block the exact keyless setup local models exist for.
    """
    if not is_local_cli_model(get_settings().default_model):
        config.require_gateway_key()


class DatasetCreate(BaseModel):
    """Request body to create an empty dataset."""

    name: str
    description: str = ""
    columns: list[str]
    label_schema: LabelSchema | None = None


class DatasetGenerate(BaseModel):
    """Request body to generate a dataset and its rows.

    ``description`` is always stored on the dataset. ``instructions``, when present,
    steer generation instead; when absent, ``description`` drives generation too, keeping
    older callers that never sent detailed guidance behaving exactly as before.
    ``label_mix`` prescribes the label distribution; omit it and the distribution follows
    whatever the prompt asks for. ``column_notes`` supplies per-column content guidance.
    """

    name: str
    description: str = ""
    instructions: str | None = None
    columns: list[str]
    column_notes: dict[str, str] | None = None
    label_schema: LabelSchema
    label_mix: dict[str, float] | None = None
    count: int


class DatasetGenerateFromVersion(BaseModel):
    """Request body to generate a dataset shaped by an evaluator version.

    The column set and label space derive from ``version_id`` so the result runs against
    that version by construction; the free-text fields only steer generated content.
    ``instructions`` drive generation while ``description`` is what gets stored.
    """

    version_id: str
    name: str
    description: str = ""
    instructions: str | None = None
    extra_columns: list[str] = []
    column_notes: dict[str, str] | None = None
    include_labels: bool = True
    label_guidance: str | None = None
    label_mix: dict[str, float] | None = None
    count: int


class RowsGenerate(BaseModel):
    """Request body to generate more rows into an existing dataset.

    Every field is an override: omitted ones fall back to the dataset's stored generation
    settings, so repeating an ask needs only ``count``. The dataset's own columns and label
    space are never overridable — they are what makes the new rows fit the existing ones.
    """

    count: int
    instructions: str | None = None
    column_notes: dict[str, str] | None = None
    label_mix: dict[str, float] | None = None
    label_guidance: str | None = None


class RowsAppend(BaseModel):
    """Request body to append plain data rows to a dataset."""

    rows: list[dict]


class LogfirePullRequest(BaseModel):
    """Request body to create a dataset from a Logfire SQL query."""

    name: str
    description: str = ""
    sql: str
    sample_n: int
    seed: int | None = None
    min_timestamp: datetime | None = None
    max_timestamp: datetime | None = None
    label_column: str | None = None
    label_schema: LabelSchema | None = None


class LogfireHostedFetchRequest(BaseModel):
    """Request body to import a hosted Logfire dataset into the local store."""

    source_name: str
    name: str | None = None
    description: str = ""


class HostedDatasetSummaryOut(BaseModel):
    """One hosted dataset as listed from the source Logfire project."""

    id: str
    name: str
    description: str | None = None
    case_count: int | None = None


class RowsLogfirePull(BaseModel):
    """Request body to pull more rows from Logfire into an existing dataset.

    Every field is an override: omitted ones fall back to the dataset's stored pull settings,
    so repeating an ask needs only an empty body. ``seed`` is the exception: omitting it always
    draws a fresh random sample rather than repeating the stored one, since reusing the same
    seed over an unwidened time window would likely resample the same rows. ``label_column`` is
    never overridable here, so a growing dataset's label meaning cannot drift between pulls.
    """

    sql: str | None = None
    sample_n: int | None = None
    seed: int | None = None
    min_timestamp: datetime | None = None
    max_timestamp: datetime | None = None


class LogfirePullOut(BaseModel):
    """The stored Logfire-pull settings for a dataset, as returned to the client."""

    model_config = ConfigDict(from_attributes=True)

    sql: str
    sample_n: int
    seed: int
    min_timestamp: datetime | None
    max_timestamp: datetime | None
    label_column: str | None


class HostedFetchOut(BaseModel):
    """The stored hosted-fetch source for a dataset, as returned to the client."""

    model_config = ConfigDict(from_attributes=True)

    source_name: str


class LogfirePushRequest(BaseModel):
    """Request body to push a dataset to Logfire's hosted dataset store."""

    name: str | None = None
    description: str | None = None
    on_conflict: Literal["update", "error"] = "update"


class DatasetUpdate(BaseModel):
    """Partial update for a dataset's metadata and shape."""

    name: str | None = None
    description: str | None = None
    columns: list[str] | None = None
    column_renames: dict[str, str] | None = None


class DatasetOut(BaseModel):
    """A dataset as returned to the client."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    name: str
    description: str
    columns: list[str]


class DatasetSummaryOut(DatasetOut):
    """A dataset in the list response, carrying its row and labeled-row counts.

    The counts are exposed only where the list endpoint can fill them from a single grouped
    query. Single-dataset endpoints return the plain ``DatasetOut`` and surface counts via
    ``GET /api/datasets/{id}/stats`` instead, so no response advertises a count it would have
    to fabricate as zero. Every field the bare ``DatasetOut`` exposed survives unchanged, so
    current callers reading only those are unaffected.
    """

    row_count: int
    labeled_count: int


class DatasetCreatedOut(BaseModel):
    """A newly created dataset paired with the number of rows persisted."""

    dataset: DatasetOut
    row_count: int


class ExportFilesResponse(BaseModel):
    """A portable export as a mapping of emitted filename to its text content."""

    files: dict[str, str]


class DatasetGenerationOut(BaseModel):
    """The stored generation settings for a dataset, as returned to the client."""

    model_config = ConfigDict(from_attributes=True)

    count: int
    instructions: str | None
    column_notes: dict | None
    label_mix: dict | None
    label_guidance: str | None
    include_labels: bool
    source_version_id: str | None


class RowOut(BaseModel):
    """A dataset row as returned to the client."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    dataset_id: str
    idx: int
    data: dict


class RowsPage(BaseModel):
    """A paginated slice of dataset rows."""

    rows: list[RowOut]
    total: int
    limit: int
    offset: int


class StatsOut(BaseModel):
    """Labeling progress for a dataset."""

    total: int
    labeled: int
    unlabeled: int
    label_distribution: dict[str, int]


def _parse_csv(
    text: str, label_column: str | None, label_schema: dict | None = None
) -> tuple[list[str], list[dict], list[dict | None]]:
    """Parse CSV text into inferred data columns, prepared rows, and per-row annotation fields."""
    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames or []
    if label_column is not None and label_column not in header:
        raise ContractError(f"label_column {label_column!r} is not one of the columns {header}.")
    columns = [name for name in header if name != label_column]
    pairs = [_prepare_row(dict(record), columns, label_column, label_schema) for record in reader]
    prepared = [p for p, _ in pairs]
    row_annotations = [a for _, a in pairs]
    return columns, prepared, row_annotations


def _parse_jsonl(
    text: str, label_column: str | None, label_schema: dict | None = None
) -> tuple[list[str], list[dict], list[dict | None]]:
    """Parse JSONL text into inferred data columns, prepared rows, and per-row annotation fields."""
    records: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContractError(f"Invalid JSON line: {exc}.") from exc
        if not isinstance(record, dict):
            raise ContractError("Every JSONL record must be an object.")
        records.append(record)

    keys: list[str] = []
    for record in records[:_JSONL_INFER_LIMIT]:
        for key in record:
            if key != label_column and key not in keys:
                keys.append(key)
    pairs = [_prepare_row(record, keys, label_column, label_schema) for record in records]
    prepared = [p for p, _ in pairs]
    row_annotations = [a for _, a in pairs]
    return keys, prepared, row_annotations


def _prepare_row(
    record: dict, columns: list[str], label_column: str | None, label_schema: dict | None = None
) -> tuple[dict, dict | None]:
    """Split a raw record into (prepared_row, annotation_fields | None).

    ``annotation_fields`` carries the label_column's value, when present, as keyword
    fields for ``Store.set_annotation`` once a label set and the row's real id exist.
    CSV values always arrive as plain strings regardless of the schema's kind, so a
    numeric label_schema coerces the string to float before deciding which field to
    populate; without a schema (or a categorical one), the value is used as-is.
    """
    prepared = {"data": {k: v for k, v in record.items() if k != label_column}}
    if label_column is not None and record.get(label_column) is not None:
        value = record[label_column]
        if (label_schema or {}).get("kind") == "numeric" and isinstance(value, str):
            value = float(value)
        fields = {"labels": [value]} if isinstance(value, str) else {"value": value}
        return prepared, {**fields, "source": LabelSource.MANUAL}
    return prepared, None


@router.get("")
async def list_datasets(store: StoreDep) -> list[DatasetSummaryOut]:
    """List every dataset with its row and labeled-row counts."""
    return [DatasetSummaryOut.model_validate(ds) for ds in store.list_datasets()]


@router.post("")
async def create_dataset(body: DatasetCreate, store: StoreDep) -> DatasetOut:
    """Create an empty dataset, with a label set if a label_schema is given."""
    dataset = store.create_dataset(
        name=body.name, description=body.description, columns=body.columns
    )
    if body.label_schema is not None:
        store.create_label_set(
            dataset.id,
            name="Labels",
            description="",
            **label_set_fields_from_schema(body.label_schema),
        )
    return DatasetOut.model_validate(dataset)


@router.get("/logfire-hosted")
async def list_logfire_hosted() -> list[HostedDatasetSummaryOut]:
    """List hosted datasets in the Logfire project the read key is scoped to."""
    summaries = await list_hosted_datasets()
    return [HostedDatasetSummaryOut.model_validate(item) for item in summaries]


@router.get("/{id}")
async def get_dataset(id: str, store: StoreDep) -> DatasetOut:
    """Return a single dataset."""
    return DatasetOut.model_validate(store.get_dataset(id))


@router.patch("/{id}")
async def update_dataset(id: str, body: DatasetUpdate, store: StoreDep) -> DatasetOut:
    """Update a dataset's metadata and shape, migrating its rows."""
    dataset = store.update_dataset(
        id,
        name=body.name,
        description=body.description,
        columns=body.columns,
        column_renames=body.column_renames,
    )
    return DatasetOut.model_validate(dataset)


@router.delete("/{id}")
async def delete_dataset(id: str, store: StoreDep) -> dict[str, str]:
    """Delete a dataset and all of its rows."""
    store.delete_dataset(id)
    return {"status": "deleted"}


@router.post("/upload")
async def upload_dataset(
    store: StoreDep,
    file: Annotated[UploadFile, File()],
    name: Annotated[str, Form()],
    label_column: Annotated[str | None, Form()] = None,
    label_schema: Annotated[str | None, Form()] = None,
) -> DatasetCreatedOut:
    """Create a dataset from an uploaded CSV, JSONL, or eval-package JSON file.

    A ``.json`` body is ambiguous: it may be a whole eval package or a stream of JSONL objects.
    A package is recognized only when the entire body parses as one JSON object that
    ``EvalPackage.from_text`` accepts; anything else (including JSONL, which is not one JSON
    value) falls through to the JSONL parser unchanged.
    """
    contents = await file.read()
    if len(contents) > _MAX_UPLOAD_BYTES:
        raise ContractError(
            f"File exceeds the {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit."
        )

    text = contents.decode("utf-8-sig")
    filename = (file.filename or "").lower()
    if filename.endswith(".csv"):
        schema_dict = _parse_label_schema(label_schema)
        columns, prepared, row_annotations = _parse_csv(text, label_column, schema_dict)
    elif filename.endswith(".json") and (package := _load_package(text)) is not None:
        columns, prepared, schema_dict, row_annotations = _import_package(
            package, label_column, label_schema
        )
    elif filename.endswith((".jsonl", ".json")):
        schema_dict = _parse_label_schema(label_schema)
        columns, prepared, row_annotations = _parse_jsonl(text, label_column, schema_dict)
    else:
        raise ContractError("Unsupported file type; upload a .csv or .jsonl file.")

    if not prepared:
        raise ContractError("File contains no data rows.")

    dataset = store.create_dataset(name=name, description="", columns=columns)
    rows = store.add_prepared_rows(dataset.id, prepared)
    if schema_dict:
        label_set = store.create_label_set(
            dataset.id,
            name="Imported labels",
            description="",
            **label_set_fields_from_schema(LabelSchema.model_validate(schema_dict)),
        )
        for row, fields in zip(rows, row_annotations):
            if fields is not None:
                store.set_annotation(label_set.id, row.id, **fields)
    return DatasetCreatedOut(dataset=DatasetOut.model_validate(dataset), row_count=len(rows))


def _load_package(text: str) -> EvalPackage | None:
    """Return the eval package a whole-JSON body describes, or None to fall through to JSONL.

    JSONL is a sequence of objects, not one JSON value, so ``json.loads`` on the whole body
    fails and the caller drops through unchanged. An otherwise-valid JSON dict that is not a
    recognizable package likewise returns None rather than erroring here.
    """
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return EvalPackage.from_text(text)
    except ContractError:
        return None


def _import_package(
    package: EvalPackage, label_column: str | None, label_schema: str | None
) -> tuple[list[str], list[dict], dict, list[dict | None]]:
    """Decode a package's dataset half into (columns, prepared_rows, label_schema, row_annotations)."""
    if label_column is not None:
        raise ContractError("label_column does not apply to an eval-package upload.")
    _name, columns, package_schema, prepared, row_annotations = package.to_dataset_fields()
    explicit = label_schema is not None and label_schema.strip()
    schema_dict = _parse_label_schema(label_schema) if explicit else package_schema
    return columns, prepared, schema_dict, row_annotations


def _parse_label_schema(raw: str | None) -> dict:
    """Parse an optional JSON label-schema form field, validating its shape."""
    if raw is None or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContractError(f"Invalid label_schema JSON: {exc}.") from exc
    try:
        return LabelSchema.model_validate(parsed).model_dump(mode="json")
    except ValueError as exc:
        raise ContractError(f"Invalid label_schema: {exc}.") from exc


@router.post("/{id}/rows")
async def append_rows(id: str, body: RowsAppend, store: StoreDep) -> list[RowOut]:
    """Append plain data rows to a dataset, returning the created rows."""
    store.get_dataset(id)
    rows = store.add_rows(id, body.rows)
    return [RowOut.model_validate(row) for row in rows]


def _check_column_notes(column_notes: dict[str, str] | None, columns: list[str]) -> None:
    """Raise ContractError if any note names a column the dataset will not have.

    A note on a column that does not exist steers nothing, so it is almost always a typo
    in the column name; surfacing it beats spending a slow generation call and silently
    dropping the guidance.
    """
    if not column_notes:
        return
    unknown = [key for key in column_notes if key not in columns]
    if unknown:
        raise ContractError(
            f"column_notes references column(s) {unknown} not in the dataset's columns {columns}."
        )


@router.post("/generate")
async def generate_dataset(body: DatasetGenerate, store: StoreDep) -> DatasetCreatedOut:
    """Generate a dataset and its rows with suggested labels."""
    _require_gateway_key_unless_local()
    if body.count > _MAX_GENERATE_COUNT:
        raise ContractError(f"count may not exceed {_MAX_GENERATE_COUNT}.")

    _check_column_notes(body.column_notes, body.columns)

    dataset = store.create_dataset(
        name=body.name,
        description=body.description,
        columns=body.columns,
    )
    # ``instructions`` steer generation when supplied; otherwise the stored description
    # doubles as the prompt, preserving the pre-``instructions`` behaviour.
    prompt = body.instructions if body.instructions is not None else body.description
    generated = await generate_rows(
        prompt,
        body.columns,
        body.label_schema,
        body.count,
        column_notes=body.column_notes,
        label_mix=body.label_mix,
    )
    prepared = [{"data": row.data} for row in generated]
    rows = store.add_prepared_rows(dataset.id, prepared)
    label_set = store.create_label_set(
        dataset.id,
        name="Generated labels",
        description="",
        **label_set_fields_from_schema(body.label_schema),
    )
    for row, generated_row in zip(rows, generated):
        store.set_annotation(
            label_set.id,
            row.id,
            **(
                {"suggested_labels": [generated_row.suggested_label]}
                if body.label_schema.kind is ScoreKind.CATEGORICAL
                else {"suggested_value": generated_row.suggested_label}
            ),
            reasoning=generated_row.reasoning,
            source=LabelSource.GENERATED,
        )
    store.set_generation(
        dataset.id,
        count=body.count,
        instructions=body.instructions,
        column_notes=body.column_notes,
        label_mix=body.label_mix,
    )
    return DatasetCreatedOut(dataset=DatasetOut.model_validate(dataset), row_count=len(rows))


@router.post("/generate-from-version")
async def generate_dataset_from_version(
    body: DatasetGenerateFromVersion, store: StoreDep
) -> DatasetCreatedOut:
    """Generate a dataset shaped by an evaluator version, runnable against it by construction."""
    _require_gateway_key_unless_local()
    if body.count > _MAX_GENERATE_COUNT:
        raise ContractError(f"count may not exceed {_MAX_GENERATE_COUNT}.")

    # A missing id surfaces as the store's NotFoundError (404), never a silent empty shape.
    version = store.get_version(body.version_id)
    columns, label_schema = dataset_shape_from_version(version, body.extra_columns)

    # Guidance on how to assign labels, and a prescribed distribution over them, are both
    # meaningless with no label space; refuse rather than drop them, so the contradiction
    # is never hidden from the caller.
    if body.label_guidance is not None and not body.include_labels:
        raise ContractError("label_guidance requires include_labels to be true.")
    if body.label_mix is not None and not body.include_labels:
        raise ContractError("label_mix requires include_labels to be true.")

    _check_column_notes(body.column_notes, columns)

    # Without labels the dataset carries no ground truth: an empty schema is the legal
    # "no ground truth" state, and the generator is told there is no label space to fill.
    generation_schema = label_schema if body.include_labels else None

    dataset = store.create_dataset(
        name=body.name,
        description=body.description,
        columns=columns,
    )
    prompt = body.instructions if body.instructions is not None else body.description
    generated = await generate_rows(
        prompt,
        columns,
        generation_schema,
        body.count,
        column_notes=body.column_notes,
        label_guidance=body.label_guidance,
        label_mix=body.label_mix,
    )
    prepared: list[dict] = [{"data": row.data} for row in generated]
    rows = store.add_prepared_rows(dataset.id, prepared)
    if body.include_labels:
        label_set = store.create_label_set(
            dataset.id,
            name="Generated labels",
            description="",
            **label_set_fields_from_schema(label_schema),
        )
        for row, generated_row in zip(rows, generated):
            store.set_annotation(
                label_set.id,
                row.id,
                **(
                    {"suggested_labels": [generated_row.suggested_label]}
                    if label_schema.kind is ScoreKind.CATEGORICAL
                    else {"suggested_value": generated_row.suggested_label}
                ),
                reasoning=generated_row.reasoning,
                source=LabelSource.GENERATED,
            )
    store.set_generation(
        dataset.id,
        count=body.count,
        instructions=body.instructions,
        column_notes=body.column_notes,
        label_mix=body.label_mix,
        label_guidance=body.label_guidance,
        include_labels=body.include_labels,
        source_version_id=body.version_id,
    )
    return DatasetCreatedOut(dataset=DatasetOut.model_validate(dataset), row_count=len(rows))


@router.post("/from-logfire")
async def create_dataset_from_logfire(
    body: LogfirePullRequest, store: StoreDep
) -> DatasetCreatedOut:
    """Create a dataset by querying Logfire, nesting child spans, and sampling top-level trees."""
    if body.sample_n < 1:
        raise ContractError(f"sample_n must be at least 1, got {body.sample_n}.")
    if not body.sql.strip():
        raise ContractError("sql must not be empty.")
    if body.label_column is not None and body.label_schema is None:
        raise ContractError("label_column requires a label_schema.")

    result = await pull_records(
        body.sql,
        body.sample_n,
        seed=body.seed,
        min_timestamp=body.min_timestamp,
        max_timestamp=body.max_timestamp,
        label_column=body.label_column,
    )
    dataset = store.create_dataset(
        name=body.name, description=body.description, columns=result.columns
    )
    rows = store.add_prepared_rows(dataset.id, result.prepared)
    if body.label_schema is not None:
        label_set = store.create_label_set(
            dataset.id,
            name="Imported labels",
            description="",
            **label_set_fields_from_schema(body.label_schema),
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
    return DatasetCreatedOut(dataset=DatasetOut.model_validate(dataset), row_count=len(rows))


@router.post("/from-logfire-hosted")
async def create_dataset_from_logfire_hosted(
    body: LogfireHostedFetchRequest, store: StoreDep
) -> DatasetCreatedOut:
    """Create a local dataset by fetching a hosted dataset from the source Logfire project."""
    source_name = body.source_name.strip()
    if not source_name:
        raise ContractError("source_name must not be empty.")

    result = await fetch_hosted_dataset(source_name)
    local_name = (body.name or "").strip() or result.name
    dataset = store.create_dataset(
        name=local_name, description=body.description, columns=result.columns
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
    store.set_hosted_fetch(dataset.id, source_name=source_name)
    return DatasetCreatedOut(dataset=DatasetOut.model_validate(dataset), row_count=len(rows))


@router.get("/{id}/logfire-pull")
async def get_dataset_logfire_pull(id: str, store: StoreDep) -> LogfirePullOut | None:
    """Return how a dataset's rows were pulled from Logfire, or null if they were not."""
    pull = store.get_logfire_pull(id)
    return None if pull is None else LogfirePullOut.model_validate(pull)


@router.post("/{id}/logfire-pull")
async def pull_more_from_logfire(id: str, body: RowsLogfirePull, store: StoreDep) -> list[RowOut]:
    """Pull more rows from Logfire into an existing dataset, appending to the rows already there.

    Steering falls back to the dataset's stored pull settings, so repeating a previous ask needs
    only an empty body. Shape does not: the newly pulled columns must match the dataset's own, so
    the new rows stay compatible with the existing ones and with any evaluator that already runs
    against them.
    """
    dataset = store.get_dataset(id)
    stored = store.get_logfire_pull(id)
    if stored is None:
        raise ContractError(f"Dataset {dataset.name!r} has no stored Logfire pull to repeat.")

    def resolve(override, attribute: str):
        return override if override is not None else getattr(stored, attribute)

    sql = resolve(body.sql, "sql")
    sample_n = resolve(body.sample_n, "sample_n")
    min_timestamp = resolve(body.min_timestamp, "min_timestamp")
    max_timestamp = resolve(body.max_timestamp, "max_timestamp")

    if sample_n < 1:
        raise ContractError(f"sample_n must be at least 1, got {sample_n}.")
    if not sql.strip():
        raise ContractError("sql must not be empty.")

    result = await pull_records(
        sql,
        sample_n,
        seed=body.seed,
        min_timestamp=min_timestamp,
        max_timestamp=max_timestamp,
        label_column=stored.label_column,
    )
    if result.columns != dataset.columns:
        raise ContractError(
            f"Pulled columns {result.columns} do not match this dataset's columns "
            f"{dataset.columns}."
        )

    rows = store.add_prepared_rows(dataset.id, result.prepared)
    # Record the ask that actually ran, so the next top-up repeats it rather than the
    # original creation call.
    store.set_logfire_pull(
        dataset.id,
        sql=result.sql,
        sample_n=result.sample_n,
        seed=result.seed,
        min_timestamp=result.min_timestamp,
        max_timestamp=result.max_timestamp,
        label_column=result.label_column,
    )
    return [RowOut.model_validate(row) for row in rows]


def _row_content_key(data: dict) -> str:
    """A canonical key for union-style dedup: rows with identical data collide.

    Hosted cases carry no stable id once imported (see ``spec.evals_to_dataset_fields``), so
    exact content match is the only signal available for "already present." Only ``data``
    is hashed -- a row's label lives in a separate ``Annotation`` now, not attached to the
    row dict, so two rows with identical input data are duplicates regardless of what
    either has (or will have) annotated against it.
    """
    return json.dumps({"data": data}, sort_keys=True, default=str)


@router.get("/{id}/hosted-fetch")
async def get_dataset_hosted_fetch(id: str, store: StoreDep) -> HostedFetchOut | None:
    """Return which hosted Logfire dataset a dataset was fetched from, or null if it was not."""
    fetch = store.get_hosted_fetch(id)
    return None if fetch is None else HostedFetchOut.model_validate(fetch)


@router.post("/{id}/hosted-fetch")
async def pull_more_from_logfire_hosted(id: str, store: StoreDep) -> list[RowOut]:
    """Refetch this dataset's hosted source and append any rows not already present.

    A union, not a sync: rows already here by content match are left completely alone (even if
    they were since relabeled or annotated, and even if they no longer exist upstream) — only
    rows the local dataset doesn't already have are appended.
    """
    dataset = store.get_dataset(id)
    stored = store.get_hosted_fetch(id)
    if stored is None:
        raise ContractError(
            f"Dataset {dataset.name!r} has no stored Logfire hosted-fetch source to repeat."
        )

    result = await fetch_hosted_dataset(stored.source_name)
    if result.columns != dataset.columns:
        raise ContractError(
            f"Hosted dataset columns {result.columns} do not match this dataset's columns "
            f"{dataset.columns}."
        )

    existing = {_row_content_key(row.data) for row in store.list_rows(dataset.id)}
    new_rows = [
        prepared
        for prepared in result.prepared
        if _row_content_key(prepared.get("data", {})) not in existing
    ]
    rows = store.add_prepared_rows(dataset.id, new_rows) if new_rows else []
    return [RowOut.model_validate(row) for row in rows]


@router.get("/{id}/generation")
async def get_dataset_generation(id: str, store: StoreDep) -> DatasetGenerationOut | None:
    """Return how a dataset's rows were generated, or null if it was uploaded or blank."""
    generation = store.get_generation(id)
    return None if generation is None else DatasetGenerationOut.model_validate(generation)


@router.post("/{id}/generate-rows")
async def generate_more_rows(id: str, body: RowsGenerate, store: StoreDep) -> list[RowOut]:
    """Generate more rows into an existing dataset, appending to the rows already there.

    Steering falls back to the dataset's stored generation settings, so asking again needs
    only a ``count``. Shape does not: the dataset's own columns and label space are used, so
    the new rows stay compatible with the existing ones and with any evaluator that already
    runs against them.
    """
    _require_gateway_key_unless_local()
    if body.count > _MAX_GENERATE_COUNT:
        raise ContractError(f"count may not exceed {_MAX_GENERATE_COUNT}.")

    dataset = store.get_dataset(id)
    stored = store.get_generation(id)

    def resolve(override, attribute: str):
        """An explicit override wins; otherwise fall back to the stored ask."""
        if override is not None:
            return override
        return getattr(stored, attribute) if stored is not None else None

    instructions = resolve(body.instructions, "instructions")
    column_notes = resolve(body.column_notes, "column_notes")
    label_mix = resolve(body.label_mix, "label_mix")
    label_guidance = resolve(body.label_guidance, "label_guidance")

    _check_column_notes(column_notes, dataset.columns)

    label_set = store.primary_label_set(id)
    label_schema = label_schema_from_label_set(label_set) if label_set is not None else None
    if label_schema is None and label_guidance is not None:
        raise ContractError(
            f"Dataset {dataset.name!r} has no label space, so label_guidance cannot apply."
        )

    prompt = instructions if instructions is not None else dataset.description
    generated = await generate_rows(
        prompt,
        dataset.columns,
        label_schema,
        body.count,
        column_notes=column_notes,
        label_guidance=label_guidance,
        label_mix=label_mix,
    )
    prepared = [{"data": row.data} for row in generated]
    rows = store.add_prepared_rows(dataset.id, prepared)
    if label_set is not None:
        for row, generated_row in zip(rows, generated):
            store.set_annotation(
                label_set.id,
                row.id,
                **(
                    {"suggested_labels": [generated_row.suggested_label]}
                    if label_schema.kind is ScoreKind.CATEGORICAL
                    else {"suggested_value": generated_row.suggested_label}
                ),
                reasoning=generated_row.reasoning,
                source=LabelSource.GENERATED,
            )

    store.set_generation(
        dataset.id,
        count=body.count,
        instructions=instructions,
        column_notes=column_notes,
        label_mix=label_mix,
        label_guidance=label_guidance,
        include_labels=label_set is not None,
        source_version_id=stored.source_version_id if stored else None,
    )
    return [RowOut.model_validate(row) for row in rows]


@router.get("/{id}/rows")
async def list_dataset_rows(
    id: str, store: StoreDep, limit: int = 100, offset: int = 0
) -> RowsPage:
    """Return a paginated slice of a dataset's rows."""
    store.get_dataset(id)
    rows = store.list_rows(id, limit=limit, offset=offset)
    total = len(store.list_rows(id))
    return RowsPage(
        rows=[RowOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.delete("/rows/{row_id}", status_code=204)
async def delete_row(row_id: str, store: StoreDep) -> None:
    """Delete a single dataset row."""
    store.delete_row(row_id)


@router.get("/{id}/stats")
async def dataset_stats(id: str, store: StoreDep) -> StatsOut:
    """Return labeling progress for a dataset, from its primary label set (if any)."""
    store.get_dataset(id)
    label_set = store.primary_label_set(id)
    if label_set is None:
        total = len(store.list_rows(id))
        return StatsOut(total=total, labeled=0, unlabeled=total, label_distribution={})
    _, total = store.annotation_progress(label_set.id)
    rows = store.list_rows(id)
    annotations = store.list_annotations_for_rows(label_set.id, [row.id for row in rows])
    distribution: dict[str, int] = {}
    for annotation in annotations:
        value = annotation_ground_truth(label_set, annotation)
        if value is None:
            continue
        key = str(value)
        distribution[key] = distribution.get(key, 0) + 1
    labeled = sum(distribution.values())
    return StatsOut(
        total=total,
        labeled=labeled,
        unlabeled=total - labeled,
        label_distribution=distribution,
    )


def _stem(name: str) -> str:
    """Derive a filesystem- and import-safe file stem from a display name."""
    cleaned = re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()
    return cleaned or "eval_package"


def _primary_ground_truth(store: Store, dataset_id: str, rows: list) -> tuple:
    """Return (label_set, annotations) for a dataset's primary label set, or (None, None).

    Ground truth now lives on ``LabelSet``/``Annotation``, not on ``DatasetRow``, so export
    must fetch it explicitly rather than reading it off the row.
    """
    label_set = store.primary_label_set(dataset_id)
    if label_set is None:
        return None, None
    annotations = store.list_annotations_for_rows(label_set.id, [row.id for row in rows])
    return label_set, annotations


@router.get("/{id}/export.py", response_model=ExportFilesResponse)
async def export_dataset_py(id: str, store: StoreDep) -> ExportFilesResponse:
    """Return a module that rebuilds this dataset as a ``pydantic_evals.Dataset``."""
    dataset = store.get_dataset(id)
    rows = store.list_rows(id)
    label_set, annotations = _primary_ground_truth(store, id, rows)
    return ExportFilesResponse(
        files={
            f"{_stem(dataset.name)}.py": render_dataset_module(
                dataset, rows, label_set=label_set, annotations=annotations
            )
        }
    )


@router.get("/{id}/export.json", response_model=ExportFilesResponse)
async def export_dataset_json(
    id: str, store: StoreDep, version_id: str | None = None, split: bool = False
) -> ExportFilesResponse:
    """Return an eval package built from this dataset, optionally merged with a version.

    With no ``version_id`` the package is dataset-only, so it carries no agent and no companion
    module. Supplying one merges in that version's agent section and rides a ``valcore_judge.py``
    alongside, pointing at whichever file carries the agent. An unknown ``version_id`` or dataset
    id surfaces the store's not-found behavior.
    """
    dataset = store.get_dataset(id)
    rows = store.list_rows(id)
    label_set, annotations = _primary_ground_truth(store, id, rows)
    package = EvalPackage.from_dataset(dataset, rows, label_set=label_set, annotations=annotations)

    version = None
    if version_id is not None:
        version = store.get_version(version_id)
        package = package.merge(EvalPackage.from_version(version))

    stem = _stem(dataset.name)
    files = package.to_text(stem, "split" if split else "bundled")
    if version is not None:
        agent_filename = f"{stem}.agent.json" if split else f"{stem}.json"
        files["valcore_judge.py"] = render_judge_module(version, agent_filename)
    return ExportFilesResponse(files=files)


@router.post("/{id}/logfire/push")
async def push_dataset_to_logfire(id: str, body: LogfirePushRequest, store: StoreDep) -> dict:
    """Push a dataset and its rows to Logfire's hosted dataset store."""
    dataset = store.get_dataset(id)
    rows = store.list_rows(id)
    return await push_dataset(
        dataset,
        rows,
        name=body.name,
        description=body.description,
        on_conflict=body.on_conflict,
    )
