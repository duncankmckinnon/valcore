"""Agent definition, trial, and response-derivation routes.

This API keeps the agent being measured distinct from evaluator agents: a trial is
ephemeral, while a saved derivation records an overlay on the source dataset.
"""

import re
from datetime import UTC, datetime
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic_ai.agent.spec import AgentSpec

from valcore import agent_spec, config
from valcore.api.deps import get_store
from valcore.errors import ContractError, NotFoundError
from valcore.factory import build_agent_from_version, execute_agent_version
from valcore.models import Agent, AgentVersion, DatasetDerivation, DerivationState
from valcore.settings import is_local_cli_model
from valcore.store import DerivedRow, Store

router = APIRouter(prefix="/api/agents", tags=["agents"])

StoreDep = Annotated[Store, Depends(get_store)]


# -- Request bodies -----------------------------------------------------------


class AgentCreate(BaseModel):
    """Payload for creating an agent under test."""

    name: str
    description: str = ""


class AgentUpdate(BaseModel):
    """Partial mutable metadata for an agent."""

    name: str | None = None
    description: str | None = None


class AgentVersionCreate(BaseModel):
    """Complete binding and spec submitted for a new agent version."""

    model_config = ConfigDict(protected_namespaces=())

    version_name: str
    notes: str = ""
    model: str
    spec: dict[str, Any]
    prompt_template: str
    required_columns: list[str]
    deps_mapping: dict[str, str] = {}


class AgentVersionUpdate(BaseModel):
    """Optional changes to an unfrozen agent version."""

    model_config = ConfigDict(protected_namespaces=())

    version_name: str | None = None
    notes: str | None = None
    model: str | None = None
    spec: dict[str, Any] | None = None
    prompt_template: str | None = None
    required_columns: list[str] | None = None
    deps_mapping: dict[str, str] | None = None


class VersionCopy(BaseModel):
    """Name assigned to a copied draft version."""

    version_name: str


class TrialRequest(BaseModel):
    """A stored row or ad-hoc inputs to execute once without persisting."""

    dataset_id: str | None = None
    row_id: str | None = None
    inputs: dict[str, Any] | None = None


class TrialEntry(BaseModel):
    """One response to persist in a derivation, optionally with ad-hoc inputs."""

    row_id: str | None = None
    inputs: dict[str, Any] | None = None
    data: dict[str, Any]
    latency_ms: int | None = None
    usage: dict[str, Any] | None = None
    error: str | None = None


class DerivationSave(BaseModel):
    """The response overlay to save for an agent version and source dataset."""

    dataset_id: str
    entries: list[TrialEntry]


class AgentSpecImportRequest(BaseModel):
    """Portable serialized spec sent for preview before a version is created."""

    content: str
    format: str


# -- Response bodies ----------------------------------------------------------


class AgentSummary(BaseModel):
    """Compact agent metadata and version state."""

    id: str
    created_at: datetime
    name: str
    description: str
    active_version_id: str | None
    version_count: int


class AgentVersionRead(BaseModel):
    """Full stored agent version with response columns derived from its spec."""

    model_config = ConfigDict(protected_namespaces=())

    id: str
    created_at: datetime
    agent_id: str
    version_name: str
    notes: str
    frozen: bool
    model: str
    spec: dict[str, Any]
    prompt_template: str
    required_columns: list[str]
    deps_mapping: dict[str, str]
    response_columns: list[str]


class AgentDetail(BaseModel):
    """An agent with all of its versions."""

    agent: AgentSummary
    versions: list[AgentVersionRead]


class DerivationRead(BaseModel):
    """A response overlay plus resolved source and agent labels."""

    id: str
    created_at: datetime
    dataset_id: str
    dataset_name: str
    agent_version_id: str
    agent_name: str
    version_name: str
    ordinal: int
    state: DerivationState
    response_columns: list[str]
    response_count: int


class DerivedRowOut(BaseModel):
    """One source row joined to its saved response."""

    row_id: str
    idx: int
    data: dict[str, Any]
    latency_ms: int | None
    error: str | None


class DerivedRowsPage(BaseModel):
    """The columns and response-joined rows for one derivation."""

    columns: list[str]
    rows: list[DerivedRowOut]


class TrialResult(BaseModel):
    """The inspectable outcome of a single unsaved agent execution."""

    prompt: str
    deps: dict[str, Any]
    output: dict[str, Any]
    response_columns: list[str]
    latency_ms: int
    usage: dict[str, Any] | None
    error: str | None


class AgentSpecExport(BaseModel):
    """A portable YAML definition and suggested filename."""

    filename: str
    content: str


class AgentSpecImport(BaseModel):
    """A parsed portable definition with its recovered valcore binding."""

    model_config = ConfigDict(protected_namespaces=())

    spec: dict[str, Any]
    model: str | None
    prompt_template: str | None
    required_columns: list[str]
    deps_mapping: dict[str, str]


def _agent_summary(agent: Agent, store: Store) -> AgentSummary:
    """Return agent metadata plus its version count."""
    return AgentSummary(
        id=agent.id,
        created_at=_utc(agent.created_at),
        name=agent.name,
        description=agent.description,
        active_version_id=agent.active_version_id,
        version_count=len(store.list_agent_versions(agent.id)),
    )


def _version_read(version: AgentVersion) -> AgentVersionRead:
    """Serialize a stored version and calculate its spec-owned output columns."""
    spec = agent_spec.parse_spec(version.spec)
    return AgentVersionRead(
        id=version.id,
        created_at=_utc(version.created_at),
        agent_id=version.agent_id,
        version_name=version.version_name,
        notes=version.notes,
        frozen=version.frozen,
        model=version.model,
        spec=version.spec,
        prompt_template=version.prompt_template,
        required_columns=version.required_columns,
        deps_mapping=version.deps_mapping,
        response_columns=agent_spec.output_column_names(spec),
    )


def _derivation_read(derivation: DatasetDerivation, store: Store) -> DerivationRead:
    """Resolve the presentation fields belonging to a derivation."""
    dataset = store.get_dataset(derivation.dataset_id)
    version = store.get_agent_version(derivation.agent_version_id)
    agent = store.get_agent(version.agent_id)
    return DerivationRead(
        id=derivation.id,
        created_at=_utc(derivation.created_at),
        dataset_id=dataset.id,
        dataset_name=dataset.name,
        agent_version_id=version.id,
        agent_name=agent.name,
        version_name=version.version_name,
        ordinal=derivation.ordinal,
        state=store.derivation_state(derivation.id),
        response_columns=derivation.response_columns,
        response_count=len(store.list_agent_responses(derivation.id)),
    )


def _slug(name: str) -> str:
    """Make a portable lowercase filename segment from a display name."""
    return re.sub(r"[^0-9a-zA-Z]+", "-", name).strip("-").lower() or "agent"


def _utc(value: datetime) -> datetime:
    """Restore SQLite's timezone-less UTC timestamps before JSON serialization."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


# -- Versions and derivations -------------------------------------------------


@router.get("/derivations", response_model=list[DerivationRead])
async def list_derivations(
    store: StoreDep,
    dataset_id: str | None = None,
    agent_version_id: str | None = None,
    include_staged: bool = False,
) -> list[DerivationRead]:
    """List derivations, optionally constrained to one dataset or version."""
    return [
        _derivation_read(derivation, store)
        for derivation in store.list_derivations(
            dataset_id=dataset_id,
            agent_version_id=agent_version_id,
            include_staged=include_staged,
        )
    ]


@router.post("/derivations/{id}/save", response_model=DerivationRead)
async def save_staged_derivation(id: str, store: StoreDep) -> DerivationRead:
    """Accept a staged response overlay and assign its saved ordinal."""
    return _derivation_read(store.save_staged_derivation(id), store)


@router.delete("/derivations/{id}", status_code=204)
async def delete_derivation(id: str, store: StoreDep) -> None:
    """Discard an unreferenced response overlay."""
    store.delete_derivation(id)


@router.get("/derivations/{id}/rows", response_model=DerivedRowsPage)
async def derived_rows(id: str, store: StoreDep) -> DerivedRowsPage:
    """Return the source inputs overlaid with this derivation's responses."""
    derivation = store.get_derivation(id)
    dataset = store.get_dataset(derivation.dataset_id)
    rows: list[DerivedRow] = store.derived_rows(id)
    return DerivedRowsPage(
        columns=[*dataset.columns, *derivation.response_columns],
        rows=[DerivedRowOut(**row.__dict__) for row in rows],
    )


@router.patch("/versions/{vid}", response_model=AgentVersionRead)
async def update_version(vid: str, body: AgentVersionUpdate, store: StoreDep) -> AgentVersionRead:
    """Apply supplied fields only to an unfrozen version."""
    return _version_read(store.update_agent_version(vid, **body.model_dump(exclude_unset=True)))


@router.post("/versions/{vid}/freeze", response_model=AgentVersionRead)
async def freeze_version(vid: str, store: StoreDep) -> AgentVersionRead:
    """Freeze an agent version against future edits."""
    return _version_read(store.freeze_agent_version(vid))


@router.post("/versions/{vid}/copy", response_model=AgentVersionRead)
async def copy_version(vid: str, body: VersionCopy, store: StoreDep) -> AgentVersionRead:
    """Copy a version into a named unfrozen active draft."""
    return _version_read(store.copy_agent_version(vid, body.version_name))


@router.delete("/versions/{vid}", status_code=204)
async def delete_version(vid: str, store: StoreDep) -> None:
    """Delete an unreferenced agent version."""
    store.delete_agent_version(vid)


@router.get("/versions/{vid}/export", response_model=AgentSpecExport)
async def export_version(vid: str, store: StoreDep) -> AgentSpecExport:
    """Export a version's spec to YAML with its valcore binding in metadata."""
    version = store.get_agent_version(vid)
    spec = agent_spec.parse_spec(version.spec)
    document = spec.model_dump(mode="json", exclude_none=True, context={"use_short_form": True})
    metadata = dict(document.get("metadata") or {})
    metadata["valcore"] = {
        "model": version.model,
        "prompt_template": version.prompt_template,
        "required_columns": version.required_columns,
        "deps_mapping": version.deps_mapping,
    }
    document["metadata"] = metadata
    agent = store.get_agent(version.agent_id)
    return AgentSpecExport(
        filename=f"{_slug(agent.name)}-{_slug(version.version_name)}.yaml",
        content=yaml.safe_dump(document, sort_keys=False),
    )


@router.post("/import", response_model=AgentSpecImport)
async def import_spec(body: AgentSpecImportRequest) -> AgentSpecImport:
    """Parse a portable agent definition without creating persistent state."""
    try:
        spec = AgentSpec.from_text(body.content, body.format)
    # Parser implementations expose several exception types; all are client contract failures.
    except Exception as exc:
        raise ContractError(f"Invalid agent spec import: {exc}") from exc
    binding = (spec.metadata or {}).get("valcore", {})
    if not isinstance(binding, dict):
        binding = {}
    try:
        return AgentSpecImport(
            spec=spec.model_dump(mode="json", context={"use_short_form": True}),
            model=binding.get("model"),
            prompt_template=binding.get("prompt_template"),
            required_columns=binding.get("required_columns") or [],
            deps_mapping=binding.get("deps_mapping") or {},
        )
    except ValidationError as exc:
        raise ContractError(f"Invalid valcore binding metadata: {exc}") from exc


@router.post("/versions/{vid}/trial", response_model=TrialResult)
async def trial_version(vid: str, body: TrialRequest, store: StoreDep) -> TrialResult:
    """Run a version once and return either its output or its model failure."""
    version = store.get_agent_version(vid)
    if not is_local_cli_model(version.model):
        config.require_gateway_key()
    if body.row_id is not None:
        row_data = store.get_row(body.row_id).data
    elif body.inputs is not None:
        row_data = body.inputs
    else:
        raise ContractError("A trial requires either row_id or inputs.")
    agent = build_agent_from_version(version)
    execution = await execute_agent_version(version, agent, row_data)
    return TrialResult(**execution.__dict__)


@router.post("/versions/{vid}/derivations", response_model=DerivationRead)
async def save_derivation(vid: str, body: DerivationSave, store: StoreDep) -> DerivationRead:
    """Save a complete response overlay, adding rows for ad-hoc entries first."""
    version = store.get_agent_version(vid)
    store.get_dataset(body.dataset_id)
    if not body.entries:
        raise ContractError("A derivation must contain at least one response.")

    stored_row_ids = [entry.row_id for entry in body.entries if entry.row_id is not None]
    duplicate_row_ids = sorted(
        row_id for row_id in set(stored_row_ids) if stored_row_ids.count(row_id) > 1
    )
    if duplicate_row_ids:
        raise ContractError(
            f"Derivation responses contain duplicate row_id values: {duplicate_row_ids}."
        )
    for row_id in stored_row_ids:
        try:
            row = store.get_row(row_id)
        except NotFoundError as exc:
            raise ContractError(
                f"Derivation responses reference missing row_id values: {[row_id]}."
            ) from exc
        if row.dataset_id != body.dataset_id:
            raise ContractError(
                "Derivation responses reference rows outside dataset "
                f"{body.dataset_id!r}: {[row_id]}."
            )

    response_columns = agent_spec.output_column_names(agent_spec.parse_spec(version.spec))
    ad_hoc_entries = [entry for entry in body.entries if entry.row_id is None]
    added_rows = iter(
        store.add_rows(body.dataset_id, [entry.inputs or {} for entry in ad_hoc_entries])
    )
    responses: list[dict[str, Any]] = []
    for entry in body.entries:
        row_id = entry.row_id or next(added_rows).id
        responses.append(
            {
                "row_id": row_id,
                "data": entry.data,
                "latency_ms": entry.latency_ms,
                "usage": entry.usage,
                "error": entry.error,
            }
        )
    derivation = store.save_derivation(
        dataset_id=body.dataset_id,
        agent_version_id=vid,
        response_columns=response_columns,
        responses=responses,
    )
    return _derivation_read(derivation, store)


# -- Agents -------------------------------------------------------------------


@router.get("", response_model=list[AgentSummary])
async def list_agents(store: StoreDep) -> list[AgentSummary]:
    """List all agents under test."""
    return [_agent_summary(agent, store) for agent in store.list_agents()]


@router.post("", response_model=AgentSummary)
async def create_agent(body: AgentCreate, store: StoreDep) -> AgentSummary:
    """Create an agent with no versions."""
    return _agent_summary(store.create_agent(body.name, body.description), store)


@router.get("/{id}/versions", response_model=list[AgentVersionRead])
async def list_versions(id: str, store: StoreDep) -> list[AgentVersionRead]:
    """List every version belonging to one agent."""
    store.get_agent(id)
    return [_version_read(version) for version in store.list_agent_versions(id)]


@router.post("/{id}/versions", response_model=AgentVersionRead)
async def create_version(id: str, body: AgentVersionCreate, store: StoreDep) -> AgentVersionRead:
    """Create a validated version and make it active."""
    return _version_read(store.create_agent_version(id, **body.model_dump()))


@router.get("/{id}", response_model=AgentDetail)
async def get_agent(id: str, store: StoreDep) -> AgentDetail:
    """Return an agent and all its stored versions."""
    agent = store.get_agent(id)
    return AgentDetail(
        agent=_agent_summary(agent, store),
        versions=[_version_read(version) for version in store.list_agent_versions(id)],
    )


@router.patch("/{id}", response_model=AgentSummary)
async def update_agent(id: str, body: AgentUpdate, store: StoreDep) -> AgentSummary:
    """Update mutable agent metadata."""
    return _agent_summary(store.update_agent(id, **body.model_dump(exclude_unset=True)), store)


@router.delete("/{id}", status_code=204)
async def delete_agent(id: str, store: StoreDep) -> None:
    """Delete an agent whose versions are not referenced by derivations."""
    store.delete_agent(id)
