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
    """Request body to generate a dataset and its rows."""

    name: str
    description: str = ""
    columns: list[str]
    label_schema: LabelSchema
    count: int


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


@router.post("/generate")
async def generate_dataset(body: DatasetGenerate, store: StoreDep) -> DatasetCreatedOut:
    """Generate a dataset and its rows with suggested labels."""
    if body.count > _MAX_GENERATE_COUNT:
        raise ContractError(f"count may not exceed {_MAX_GENERATE_COUNT}.")

    dataset = store.create_dataset(
        name=body.name,
        description=body.description,
        columns=body.columns,
        label_schema=body.label_schema.model_dump(mode="json"),
    )
    generated = await generate_rows(body.description, body.columns, body.label_schema, body.count)
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
    return DatasetCreatedOut(dataset=DatasetOut.model_validate(dataset), row_count=len(rows))


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
