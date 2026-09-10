"""Label set (annotation contract) CRUD and per-row annotation endpoints."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from valcore.api.deps import get_store
from valcore.models import ScoreKind
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
