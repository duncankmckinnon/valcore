"""Evaluator CRUD, version management, generation, refinement, and export routes."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from valcore import generator
from valcore.api.deps import get_store
from valcore.errors import FrozenVersionError
from valcore.export import render_script
from valcore.generator import GeneratedConfig, RefinedConfig
from valcore.models import CapabilitySpec, OutputField, ScoreKind
from valcore.store import Store

router = APIRouter(prefix="/api/evaluators", tags=["evaluators"])

StoreDep = Annotated[Store, Depends(get_store)]


# -- Request bodies -----------------------------------------------------------


class EvaluatorCreate(BaseModel):
    """Payload to create a new evaluator."""

    name: str
    description: str = ""


class VersionCreate(BaseModel):
    """Full evaluator version configuration submitted when creating a version."""

    version_name: str
    notes: str = ""
    model: str
    instructions: str
    prompt_template: str
    required_columns: list[str]
    output_fields: list[OutputField]
    score_field: str
    score_kind: ScoreKind
    score_labels: list[str] | None = None
    score_minimum: float | None = None
    score_maximum: float | None = None
    capabilities: list[CapabilitySpec] = []
    tools: list[str] = []


class VersionUpdate(BaseModel):
    """Partial update for an unfrozen version; unset fields are left untouched."""

    version_name: str | None = None
    notes: str | None = None
    model: str | None = None
    instructions: str | None = None
    prompt_template: str | None = None
    required_columns: list[str] | None = None
    output_fields: list[OutputField] | None = None
    score_field: str | None = None
    score_kind: ScoreKind | None = None
    score_labels: list[str] | None = None
    score_minimum: float | None = None
    score_maximum: float | None = None
    capabilities: list[CapabilitySpec] | None = None
    tools: list[str] | None = None


class GenerateRequest(BaseModel):
    """Natural-language criteria (and optional dataset columns) to generate a config from."""

    criteria: str
    columns: list[str] | None = None


class RefineRequest(BaseModel):
    """An existing config plus a natural-language instruction describing the change."""

    config: GeneratedConfig
    instruction: str


# -- Response bodies ----------------------------------------------------------


class VersionSummary(BaseModel):
    """Compact view of a version used in evaluator listings."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    version_name: str
    model: str
    score_kind: ScoreKind
    frozen: bool
    created_at: datetime


class VersionRead(BaseModel):
    """Full serialized view of an evaluator version."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    evaluator_id: str
    version_name: str
    notes: str
    frozen: bool
    model: str
    instructions: str
    prompt_template: str
    required_columns: list[str]
    output_fields: list[dict]
    score_field: str
    score_kind: ScoreKind
    score_labels: list[str] | None
    score_minimum: float | None
    score_maximum: float | None
    capabilities: list[dict]
    tools: list[str]


class EvaluatorSummary(BaseModel):
    """An evaluator with a summary of its active version, for the list view."""

    id: str
    name: str
    description: str
    created_at: datetime
    active_version_id: str | None
    active_version: VersionSummary | None


class EvaluatorDetail(BaseModel):
    """An evaluator with every one of its versions."""

    id: str
    name: str
    description: str
    created_at: datetime
    active_version_id: str | None
    versions: list[VersionRead]


class ExportResponse(BaseModel):
    """The rendered standalone script source for a version."""

    source: str


# -- Evaluators ---------------------------------------------------------------


@router.get("", response_model=list[EvaluatorSummary])
async def list_evaluators(store: StoreDep) -> list[EvaluatorSummary]:
    """List every evaluator alongside a summary of its active version."""
    summaries: list[EvaluatorSummary] = []
    for evaluator in store.list_evaluators():
        active: VersionSummary | None = None
        if evaluator.active_version_id is not None:
            version = store.get_version(evaluator.active_version_id)
            active = VersionSummary.model_validate(version)
        summaries.append(
            EvaluatorSummary(
                id=evaluator.id,
                name=evaluator.name,
                description=evaluator.description,
                created_at=evaluator.created_at,
                active_version_id=evaluator.active_version_id,
                active_version=active,
            )
        )
    return summaries


@router.post("", response_model=EvaluatorSummary)
async def create_evaluator(body: EvaluatorCreate, store: StoreDep) -> EvaluatorSummary:
    """Create a new evaluator with no versions yet."""
    evaluator = store.create_evaluator(name=body.name, description=body.description)
    return EvaluatorSummary(
        id=evaluator.id,
        name=evaluator.name,
        description=evaluator.description,
        created_at=evaluator.created_at,
        active_version_id=evaluator.active_version_id,
        active_version=None,
    )


@router.get("/{id}", response_model=EvaluatorDetail)
async def get_evaluator(id: str, store: StoreDep) -> EvaluatorDetail:
    """Return an evaluator with all of its versions."""
    evaluator = store.get_evaluator(id)
    versions = [VersionRead.model_validate(v) for v in store.list_versions(id)]
    return EvaluatorDetail(
        id=evaluator.id,
        name=evaluator.name,
        description=evaluator.description,
        created_at=evaluator.created_at,
        active_version_id=evaluator.active_version_id,
        versions=versions,
    )


@router.delete("/{id}", status_code=204)
async def delete_evaluator(id: str, store: StoreDep) -> None:
    """Delete an evaluator and all of its versions."""
    store.delete_evaluator(id)


# -- Versions -----------------------------------------------------------------


@router.get("/{id}/versions", response_model=list[VersionRead])
async def list_versions(id: str, store: StoreDep) -> list[VersionRead]:
    """List every version of an evaluator."""
    store.get_evaluator(id)
    return [VersionRead.model_validate(v) for v in store.list_versions(id)]


@router.post("/{id}/versions", response_model=VersionRead)
async def create_version(id: str, body: VersionCreate, store: StoreDep) -> VersionRead:
    """Create a version from a full config body, making it the active version.

    Invalid configuration raises ``ConfigError``, surfaced as a 422 error envelope.
    """
    version = store.create_version(id, **body.model_dump())
    return VersionRead.model_validate(version)


@router.patch("/versions/{vid}", response_model=VersionRead)
async def update_version(vid: str, body: VersionUpdate, store: StoreDep) -> VersionRead:
    """Update an unfrozen version.

    A frozen version raises ``FrozenVersionError`` (409); save it as a new version first.
    """
    version = store.get_version(vid)
    if version.frozen:
        raise FrozenVersionError(
            f"Version {vid!r} is frozen and cannot be edited; save it as a new version "
            "(POST /versions/{vid}/copy) before making changes."
        )
    updated = store.update_version(vid, **body.model_dump(exclude_unset=True))
    return VersionRead.model_validate(updated)


@router.post("/versions/{vid}/copy", response_model=VersionRead)
async def copy_version(vid: str, store: StoreDep) -> VersionRead:
    """Duplicate a version as a new unfrozen draft (the 'save as new version' path)."""
    source = store.get_version(vid)
    config = {
        "version_name": source.version_name,
        "notes": source.notes,
        "model": source.model,
        "instructions": source.instructions,
        "prompt_template": source.prompt_template,
        "required_columns": source.required_columns,
        "output_fields": source.output_fields,
        "score_field": source.score_field,
        "score_kind": source.score_kind,
        "score_labels": source.score_labels,
        "score_minimum": source.score_minimum,
        "score_maximum": source.score_maximum,
        "capabilities": source.capabilities,
        "tools": source.tools,
    }
    draft = store.create_version(source.evaluator_id, **config)
    return VersionRead.model_validate(draft)


@router.get("/versions/{vid}/export", response_model=ExportResponse)
async def export_version(vid: str, store: StoreDep) -> ExportResponse:
    """Return the standalone Python script source for a version as JSON."""
    version = store.get_version(vid)
    return ExportResponse(source=render_script(version))


# -- Generation & refinement --------------------------------------------------


@router.post("/generate", response_model=GeneratedConfig)
async def generate(body: GenerateRequest) -> GeneratedConfig:
    """Generate an editable config draft from criteria without persisting anything.

    Used by the 'new evaluator' flow, which generates a draft before the evaluator
    exists, so no id is required. Calls an LLM and can take tens of seconds; the UI
    presents the returned config as an editable draft saved as a version separately.
    """
    return await generator.generate_config(body.criteria, columns=body.columns)


@router.post("/{id}/generate", response_model=GeneratedConfig)
async def generate_version(id: str, body: GenerateRequest, store: StoreDep) -> GeneratedConfig:
    """Generate an editable config draft from criteria without persisting it.

    Calls an LLM and can take tens of seconds; the UI presents the returned config as
    an editable draft that the user saves as a version separately.
    """
    store.get_evaluator(id)
    return await generator.generate_config(body.criteria, columns=body.columns)


@router.post("/refine", response_model=RefinedConfig)
async def refine_version(body: RefineRequest) -> RefinedConfig:
    """Apply a natural-language change request to a config without persisting it.

    Calls an LLM and can take tens of seconds. The response includes ``changed_fields``
    for the diff view; nothing is saved until the user submits a new version.
    """
    return await generator.refine_config(body.config, body.instruction)
