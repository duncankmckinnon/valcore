"""Dataset CRUD, file upload, generation, and labeling endpoints."""

import csv
import io
import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, ConfigDict

from valcore.api.deps import get_store
from valcore.datagen import generate_rows
from valcore.errors import ContractError
from valcore.models import LabelSchema, LabelSource
from valcore.schema_migration import label_matches_schema
from valcore.seeding import dataset_shape_from_version
from valcore.store import Store

router = APIRouter(prefix="/api/datasets", tags=["datasets"])

StoreDep = Annotated[Store, Depends(get_store)]

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_MAX_GENERATE_COUNT = 200
_JSONL_INFER_LIMIT = 50


class DatasetCreate(BaseModel):
    """Request body to create an empty dataset."""

    name: str
    description: str = ""
    columns: list[str]
    label_schema: LabelSchema


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


class RowPatch(BaseModel):
    """Request body to relabel or annotate a single row."""

    label: str | float | None = None
    note: str | None = None
    accept_suggestion: bool = False
    clear_label: bool = False
    data: dict | None = None


class DatasetUpdate(BaseModel):
    """Partial update for a dataset's metadata and shape."""

    name: str | None = None
    description: str | None = None
    columns: list[str] | None = None
    column_renames: dict[str, str] | None = None
    label_schema: LabelSchema | None = None
    force: bool = False


class DatasetOut(BaseModel):
    """A dataset as returned to the client."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    name: str
    description: str
    columns: list[str]
    label_schema: dict


class DatasetCreatedOut(BaseModel):
    """A newly created dataset paired with the number of rows persisted."""

    dataset: DatasetOut
    row_count: int


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
    """A dataset row with its labels as returned to the client."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    dataset_id: str
    idx: int
    data: dict
    label: dict | None
    suggested_label: dict | None
    label_reasoning: str | None
    label_source: LabelSource | None
    note: str | None


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


def _parse_csv(text: str, label_column: str | None) -> tuple[list[str], list[dict]]:
    """Parse CSV text into inferred data columns and prepared row dicts."""
    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames or []
    if label_column is not None and label_column not in header:
        raise ContractError(f"label_column {label_column!r} is not one of the columns {header}.")
    columns = [name for name in header if name != label_column]
    prepared = [_prepare_row(dict(record), columns, label_column) for record in reader]
    return columns, prepared


def _parse_jsonl(text: str, label_column: str | None) -> tuple[list[str], list[dict]]:
    """Parse JSONL text into inferred data columns and prepared row dicts."""
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
    prepared = [_prepare_row(record, keys, label_column) for record in records]
    return keys, prepared


def _prepare_row(record: dict, columns: list[str], label_column: str | None) -> dict:
    """Split a raw record into a prepared row, moving any label column into a manual label."""
    fields: dict = {"data": {k: v for k, v in record.items() if k != label_column}}
    if label_column is not None and record.get(label_column) is not None:
        fields["label"] = {"value": record[label_column]}
        fields["label_source"] = LabelSource.MANUAL
    return fields


@router.get("")
async def list_datasets(store: StoreDep) -> list[DatasetOut]:
    """List every dataset."""
    return [DatasetOut.model_validate(ds) for ds in store.list_datasets()]


@router.post("")
async def create_dataset(body: DatasetCreate, store: StoreDep) -> DatasetOut:
    """Create an empty dataset."""
    dataset = store.create_dataset(
        name=body.name,
        description=body.description,
        columns=body.columns,
        label_schema=body.label_schema.model_dump(mode="json"),
    )
    return DatasetOut.model_validate(dataset)


@router.get("/{id}")
async def get_dataset(id: str, store: StoreDep) -> DatasetOut:
    """Return a single dataset."""
    return DatasetOut.model_validate(store.get_dataset(id))


@router.patch("/{id}")
async def update_dataset(id: str, body: DatasetUpdate, store: StoreDep) -> DatasetOut:
    """Update a dataset's metadata and shape, migrating its rows."""
    schema = body.label_schema.model_dump(mode="json") if body.label_schema is not None else None
    dataset = store.update_dataset(
        id,
        name=body.name,
        description=body.description,
        columns=body.columns,
        column_renames=body.column_renames,
        label_schema=schema,
        force=body.force,
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
    """Create a dataset from an uploaded CSV or JSONL file."""
    contents = await file.read()
    if len(contents) > _MAX_UPLOAD_BYTES:
        raise ContractError(
            f"File exceeds the {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit."
        )

    text = contents.decode("utf-8-sig")
    filename = (file.filename or "").lower()
    if filename.endswith(".csv"):
        columns, prepared = _parse_csv(text, label_column)
    elif filename.endswith((".jsonl", ".json")):
        columns, prepared = _parse_jsonl(text, label_column)
    else:
        raise ContractError("Unsupported file type; upload a .csv or .jsonl file.")

    if not prepared:
        raise ContractError("File contains no data rows.")

    schema_dict = _parse_label_schema(label_schema)
    dataset = store.create_dataset(
        name=name, description="", columns=columns, label_schema=schema_dict
    )
    rows = store.add_prepared_rows(dataset.id, prepared)
    return DatasetCreatedOut(dataset=DatasetOut.model_validate(dataset), row_count=len(rows))


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
    if body.count > _MAX_GENERATE_COUNT:
        raise ContractError(f"count may not exceed {_MAX_GENERATE_COUNT}.")

    _check_column_notes(body.column_notes, body.columns)

    dataset = store.create_dataset(
        name=body.name,
        description=body.description,
        columns=body.columns,
        label_schema=body.label_schema.model_dump(mode="json"),
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
    prepared = [
        {
            "data": row.data,
            "suggested_label": {"value": row.suggested_label},
            "label_reasoning": row.reasoning,
            "label_source": LabelSource.GENERATED,
        }
        for row in generated
    ]
    rows = store.add_prepared_rows(dataset.id, prepared)
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
    stored_schema = label_schema.model_dump(mode="json") if body.include_labels else {}
    generation_schema = label_schema if body.include_labels else None

    dataset = store.create_dataset(
        name=body.name,
        description=body.description,
        columns=columns,
        label_schema=stored_schema,
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
    prepared: list[dict] = []
    for row in generated:
        fields: dict = {
            "data": row.data,
            "label_reasoning": row.reasoning,
            "label_source": LabelSource.GENERATED,
        }
        if body.include_labels:
            fields["suggested_label"] = {"value": row.suggested_label}
        prepared.append(fields)
    rows = store.add_prepared_rows(dataset.id, prepared)
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

    # An empty stored schema is the legal "no ground truth" state, so there is no label
    # space to fill and nothing to say about how labels are assigned.
    label_schema = (
        LabelSchema.model_validate(dataset.label_schema) if dataset.label_schema else None
    )
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
    prepared: list[dict] = []
    for row in generated:
        fields: dict = {
            "data": row.data,
            "label_reasoning": row.reasoning,
            "label_source": LabelSource.GENERATED,
        }
        if label_schema is not None:
            fields["suggested_label"] = {"value": row.suggested_label}
        prepared.append(fields)
    rows = store.add_prepared_rows(dataset.id, prepared)

    # Record the ask that actually ran, so the next top-up repeats it rather than the
    # original creation call.
    store.set_generation(
        dataset.id,
        count=body.count,
        instructions=instructions,
        column_notes=column_notes,
        label_mix=label_mix,
        label_guidance=label_guidance,
        include_labels=label_schema is not None,
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
    _, total = store.labeled_count(id)
    return RowsPage(
        rows=[RowOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/rows/{row_id}")
async def patch_row(row_id: str, body: RowPatch, store: StoreDep) -> RowOut:
    """Relabel or annotate a single dataset row."""
    row = store.get_row(row_id)
    updates: dict = {}

    if body.accept_suggestion:
        if row.suggested_label is None:
            raise ContractError("Row has no suggested label to accept.")
        updates["label"] = row.suggested_label
        updates["label_source"] = LabelSource.ACCEPTED

    # A null ``label`` is indistinguishable from an omitted one, so clearing needs an
    # explicit flag rather than relying on ``label=None``.
    if body.clear_label:
        updates["label"] = None
        updates["label_source"] = None

    if body.label is not None:
        dataset = store.get_dataset(row.dataset_id)
        schema = LabelSchema.model_validate(dataset.label_schema)
        if not label_matches_schema(body.label, schema):
            raise ContractError(
                f"Label {body.label!r} is not valid for this dataset's label schema."
            )
        updates["label"] = {"value": body.label}
        updates["label_source"] = LabelSource.MANUAL

    if body.data is not None:
        dataset = store.get_dataset(row.dataset_id)
        unknown = [key for key in body.data if key not in dataset.columns]
        if unknown:
            raise ContractError(f"Unknown columns for this dataset: {unknown}.")
        updates["data"] = {**row.data, **body.data}

    if body.note is not None:
        updates["note"] = body.note

    if not updates:
        return RowOut.model_validate(row)
    return RowOut.model_validate(store.update_row(row_id, **updates))


@router.delete("/rows/{row_id}", status_code=204)
async def delete_row(row_id: str, store: StoreDep) -> None:
    """Delete a single dataset row."""
    store.delete_row(row_id)


@router.get("/{id}/stats")
async def dataset_stats(id: str, store: StoreDep) -> StatsOut:
    """Return labeling progress for a dataset."""
    store.get_dataset(id)
    labeled, total = store.labeled_count(id)
    return StatsOut(
        total=total,
        labeled=labeled,
        unlabeled=total - labeled,
        label_distribution=store.label_distribution(id),
    )
