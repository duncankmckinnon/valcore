"""Label set (annotation contract) CRUD and per-row annotation endpoints."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from valcore.api.deps import get_store
from valcore.errors import ContractError
from valcore.models import LabelSource, ScoreKind
from valcore.store import Store

router = APIRouter(prefix="/api", tags=["annotations"])

StoreDep = Annotated[Store, Depends(get_store)]


class AnnotationLabelIn(BaseModel):
    """One label in a label set creation request: its name and criteria description."""

    name: str
    description: str


class LabelSetCreate(BaseModel):
    """Request body to create a label set (annotation contract) on a dataset."""

    name: str
    description: str = ""
    kind: ScoreKind
    labels: list[AnnotationLabelIn] | None = None
    minimum: float | None = None
    maximum: float | None = None


class LabelSetUpdate(BaseModel):
    """Partial update for a label set's name/description; its label space is fixed."""

    name: str | None = None
    description: str | None = None


class LabelSetOut(BaseModel):
    """A label set as returned to the client."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    dataset_id: str
    name: str
    description: str
    kind: ScoreKind
    labels: list[dict] | None
    minimum: float | None
    maximum: float | None


class LabelSetProgressOut(LabelSetOut):
    """A label set carrying its annotation progress, as returned by the list endpoint."""

    annotated_count: int
    row_count: int


class AnnotationPut(BaseModel):
    """Request body to create or replace the annotation for one row under one label set."""

    labels: list[str] = []
    value: float | None = None
    description: str | None = None


class AnnotationOut(BaseModel):
    """A row's annotation under one label set, as returned to the client."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    label_set_id: str
    dataset_row_id: str
    labels: list[str]
    value: float | None
    suggested_labels: list[str] | None
    suggested_value: float | None
    source: LabelSource | None
    reasoning: str | None
    description: str | None


class AnnotationRowOut(BaseModel):
    """One dataset row alongside its annotation (if any) for a label set."""

    row_id: str
    idx: int
    data: dict
    annotation: AnnotationOut | None


class AnnotationRowsPage(BaseModel):
    """A paginated slice of a label set's queue: dataset rows plus their annotations."""

    rows: list[AnnotationRowOut]
    total: int
    annotated_count: int
    limit: int
    offset: int


@router.post("/datasets/{dataset_id}/label-sets")
async def create_label_set(dataset_id: str, body: LabelSetCreate, store: StoreDep) -> LabelSetOut:
    """Create a new label set (annotation contract) on a dataset."""
    label_set = store.create_label_set(
        dataset_id,
        name=body.name,
        description=body.description,
        kind=body.kind,
        labels=[label.model_dump() for label in body.labels] if body.labels else None,
        minimum=body.minimum,
        maximum=body.maximum,
    )
    return LabelSetOut.model_validate(label_set)


@router.get("/datasets/{dataset_id}/label-sets")
async def list_label_sets(dataset_id: str, store: StoreDep) -> list[LabelSetProgressOut]:
    """List a dataset's label sets, each carrying its annotation progress."""
    store.get_dataset(dataset_id)
    out: list[LabelSetProgressOut] = []
    for label_set in store.list_label_sets(dataset_id):
        annotated, total = store.annotation_progress(label_set.id)
        out.append(
            LabelSetProgressOut.model_validate(
                {
                    **LabelSetOut.model_validate(label_set).model_dump(),
                    "annotated_count": annotated,
                    "row_count": total,
                }
            )
        )
    return out


@router.get("/label-sets/{id}")
async def get_label_set(id: str, store: StoreDep) -> LabelSetOut:
    """Return a single label set."""
    return LabelSetOut.model_validate(store.get_label_set(id))


@router.patch("/label-sets/{id}")
async def update_label_set(id: str, body: LabelSetUpdate, store: StoreDep) -> LabelSetOut:
    """Rename or redescribe a label set."""
    label_set = store.update_label_set(id, name=body.name, description=body.description)
    return LabelSetOut.model_validate(label_set)


@router.delete("/label-sets/{id}", status_code=204)
async def delete_label_set(id: str, store: StoreDep) -> None:
    """Delete a label set and every annotation recorded against it."""
    store.delete_label_set(id)


@router.get("/label-sets/{id}/rows")
async def list_label_set_rows(
    id: str, store: StoreDep, limit: int = 100, offset: int = 0
) -> AnnotationRowsPage:
    """Paginated dataset rows for a label set's queue, each with its annotation if any."""
    label_set = store.get_label_set(id)
    rows = store.list_rows(label_set.dataset_id, limit=limit, offset=offset)
    annotated_count, total = store.annotation_progress(id)
    by_row = {
        annotation.dataset_row_id: annotation
        for annotation in store.list_annotations_for_rows(id, [row.id for row in rows])
    }
    return AnnotationRowsPage(
        rows=[
            AnnotationRowOut(
                row_id=row.id,
                idx=row.idx,
                data=row.data,
                annotation=AnnotationOut.model_validate(by_row[row.id])
                if row.id in by_row
                else None,
            )
            for row in rows
        ],
        total=total,
        annotated_count=annotated_count,
        limit=limit,
        offset=offset,
    )


@router.get("/label-sets/{label_set_id}/rows/{row_id}/annotation")
async def get_annotation(label_set_id: str, row_id: str, store: StoreDep) -> AnnotationOut | None:
    """Return the annotation for one row under one label set, or null if unset."""
    label_set = store.get_label_set(label_set_id)
    row = store.get_row(row_id)
    if row.dataset_id != label_set.dataset_id:
        raise ContractError(
            f"Row {row_id!r} belongs to dataset {row.dataset_id!r}, not label set {label_set_id!r}'s dataset {label_set.dataset_id!r}."
        )
    annotation = store.get_annotation(label_set_id, row_id)
    return None if annotation is None else AnnotationOut.model_validate(annotation)


@router.put("/label-sets/{label_set_id}/rows/{row_id}/annotation")
async def put_annotation(
    label_set_id: str, row_id: str, body: AnnotationPut, store: StoreDep
) -> AnnotationOut:
    """Create or replace the annotation for one row under one label set."""
    label_set = store.get_label_set(label_set_id)
    row = store.get_row(row_id)
    if row.dataset_id != label_set.dataset_id:
        raise ContractError(
            f"Row {row_id!r} belongs to dataset {row.dataset_id!r}, not label set {label_set_id!r}'s dataset {label_set.dataset_id!r}."
        )
    annotation = store.set_annotation(
        label_set_id,
        row_id,
        labels=body.labels,
        value=body.value,
        description=body.description,
        source=LabelSource.MANUAL,
    )
    return AnnotationOut.model_validate(annotation)


@router.delete("/label-sets/{label_set_id}/rows/{row_id}/annotation", status_code=204)
async def delete_annotation(label_set_id: str, row_id: str, store: StoreDep) -> None:
    """Clear the annotation for one row under one label set, if any."""
    label_set = store.get_label_set(label_set_id)
    row = store.get_row(row_id)
    if row.dataset_id != label_set.dataset_id:
        raise ContractError(
            f"Row {row_id!r} belongs to dataset {row.dataset_id!r}, not label set {label_set_id!r}'s dataset {label_set.dataset_id!r}."
        )
    store.clear_annotation(label_set_id, row_id)
