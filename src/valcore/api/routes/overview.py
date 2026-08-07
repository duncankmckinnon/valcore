"""Read-only aggregate endpoint backing the Overview page.

The page needs a single store-wide snapshot — entity counts, labeled progress, and the
best/latest completed run — without issuing a query per dataset. This router exposes that
one aggregate; every count is computed server-side by ``Store.overview``.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from valcore.api.deps import get_store
from valcore.models import RunStatus
from valcore.store import Store

router = APIRouter(prefix="/api/overview", tags=["overview"])

StoreDep = Annotated[Store, Depends(get_store)]


class LatestRunOut(BaseModel):
    """The most recently finished run, as returned to the client."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    dataset_name: str
    status: RunStatus
    accuracy: float | None
    finished_at: datetime


class OverviewOut(BaseModel):
    """Store-wide aggregate counts for the Overview page."""

    model_config = ConfigDict(from_attributes=True)

    evaluator_count: int
    dataset_count: int
    run_count: int
    total_rows: int
    labeled_rows: int
    best_accuracy: float | None
    latest_run: LatestRunOut | None


@router.get("")
async def get_overview(store: StoreDep) -> OverviewOut:
    """Return the store-wide aggregate snapshot."""
    return OverviewOut.model_validate(store.overview())
