"""SQLite persistence for valcore entities."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from sqlalchemy import delete, event, or_, update
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, func, select
from sqlmodel import create_engine as _sqlmodel_create_engine

from valcore import settings
from valcore.errors import (
    ContractError,
    FrozenVersionError,
    NotFoundError,
    ReferencedError,
    SyncConflictError,
)
from valcore.models import (
    Agent,
    AgentPromptSyncLink,
    AgentResponse,
    AgentVersion,
    Annotation,
    Dataset,
    DatasetDerivation,
    DatasetGeneration,
    DatasetHostedFetch,
    DatasetLogfirePull,
    DatasetRow,
    DerivationRole,
    DerivationState,
    DerivationStatus,
    Evaluator,
    EvaluatorVersion,
    ExperimentRun,
    LabelSet,
    LabelSource,
    Run,
    RunDerivation,
    RunKind,
    RunResult,
    RunStatus,
    ScoreKind,
    annotation_ground_truth,
    validate_agent_version,
    validate_annotation,
    validate_label_set,
    validate_version,
)
from valcore.schema_migration import apply_column_changes


def create_engine(db_path: Path | str | None = None) -> Engine:
    """Create a WAL-mode SQLite engine safe for cross-thread use."""
    if db_path is None:
        db_path = settings.get_settings().db_path
    engine = _sqlmodel_create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def init_db(engine: Engine) -> None:
    """Create every SQLModel table on the given engine."""
    SQLModel.metadata.create_all(engine)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Yield a session, committing on success and rolling back on error."""
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


_Entity = TypeVar("_Entity", bound=SQLModel)


def _require(session: Session, model: type[_Entity], id: str) -> _Entity:
    """Return the row with ``id`` or raise NotFoundError."""
    entity = session.get(model, id)
    if entity is None:
        raise NotFoundError(f"{model.__name__} {id!r} does not exist.")
    return entity


def _check_prompt_sync_local_state(
    session: Session,
    agent: Agent,
    expected_active_version_id: str | None,
    expected_local_texts: dict[str, str],
) -> None:
    """Reject a stale active version or an in-place edit to either template."""
    if agent.active_version_id != expected_active_version_id or not agent.active_version_id:
        raise SyncConflictError("The active agent version changed; inspect sync again.")
    version = session.get(AgentVersion, agent.active_version_id)
    if version is None or version.agent_id != agent.id:
        raise SyncConflictError("The active agent version changed; inspect sync again.")
    instructions = version.spec.get("instructions") if isinstance(version.spec, dict) else None
    if (
        not isinstance(instructions, str)
        or instructions != expected_local_texts.get("instructions")
        or version.prompt_template != expected_local_texts.get("input_template")
    ):
        raise SyncConflictError("An agent template changed; inspect sync again.")


def _begin_prompt_sync_write(session: Session) -> None:
    """Serialize SQLite writers before reading the active version or its templates."""
    session.connection().exec_driver_sql("BEGIN IMMEDIATE")


def _prompt_sync_cursor_values(field_updates: dict[str, dict[str, object]]) -> dict[str, object]:
    """Translate selected template cursor updates into model column values."""
    values: dict[str, object] = {}
    for key, fields in field_updates.items():
        if key not in {"instructions", "input_template"}:
            raise ValueError(f"Unknown prompt sync template key: {key!r}.")
        for field, value in fields.items():
            if field not in {"base_text", "remote_version"}:
                raise ValueError(f"Unknown prompt sync cursor field: {field!r}.")
            values[f"{key}_{field}"] = value
    return values


def _advance_prompt_sync_cursor(
    session: Session,
    agent_id: str,
    expected_link_id: str,
    expected_generation: int,
    field_updates: dict[str, dict[str, object]],
    *,
    agent_version_id: str | None = None,
) -> AgentPromptSyncLink:
    """Compare and advance a cursor in the caller's transaction."""
    values = _prompt_sync_cursor_values(field_updates)
    values["generation"] = AgentPromptSyncLink.generation + 1
    values["updated_at"] = datetime.now(UTC)
    if agent_version_id is not None:
        values["agent_version_id"] = agent_version_id
    result = session.exec(
        update(AgentPromptSyncLink)
        .where(
            AgentPromptSyncLink.agent_id == agent_id,
            AgentPromptSyncLink.id == expected_link_id,
            AgentPromptSyncLink.generation == expected_generation,
        )
        .values(**values)
    )
    if result.rowcount != 1:
        raise SyncConflictError("The prompt sync cursor changed; inspect sync again.")
    session.expire_all()
    link = session.exec(
        select(AgentPromptSyncLink).where(AgentPromptSyncLink.agent_id == agent_id)
    ).one()
    return link


def _raise_referenced(runs: list[Run], noun: str) -> None:
    """Raise ReferencedError naming the runs that block a delete."""
    run_ids = [run.id for run in runs]
    plural = "run" if len(run_ids) == 1 else "runs"
    raise ReferencedError(
        f"{len(run_ids)} {plural} reference this {noun}; delete them first.",
        detail={"run_count": len(run_ids), "run_ids": run_ids},
    )


def _raise_referenced_by_derivations(derivations: list[DatasetDerivation], noun: str) -> None:
    """Raise ReferencedError naming the derivations that block a delete."""
    derivation_ids = [derivation.id for derivation in derivations]
    plural = "derivation" if len(derivation_ids) == 1 else "derivations"
    raise ReferencedError(
        f"{len(derivation_ids)} {plural} reference this {noun}; delete them first.",
        detail={"derivation_count": len(derivation_ids), "derivation_ids": derivation_ids},
    )


@dataclass
class DatasetSummary:
    """A dataset carrying its row and labeled-row counts, as returned by ``list_datasets``.

    The counts are additive: every field the bare ``Dataset`` exposed survives with the
    same name and type, so callers reading only those are unaffected.
    """

    id: str
    created_at: datetime
    name: str
    description: str
    columns: list[str]
    row_count: int
    labeled_count: int

    def model_dump(self) -> dict:
        """Match the ``Dataset.model_dump`` the CLI relied on before counts were added."""
        return asdict(self)


@dataclass
class DerivedRow:
    """One row of a derived view: a dataset row's inputs joined to an agent's response.

    Responses are an overlay, so this view is computed as a join rather than stored as a
    dataset copy; that keeps every derivation tied to the exact same source inputs.
    """

    row_id: str
    idx: int
    data: dict
    latency_ms: int | None
    error: str | None

    def model_dump(self) -> dict:
        """Return this joined row in the model-dump shape used by store callers."""
        return asdict(self)


@dataclass
class LatestRun:
    """The most recently finished run, flattened for the overview aggregate."""

    id: str
    dataset_name: str
    status: RunStatus
    accuracy: float | None
    finished_at: datetime


@dataclass
class Overview:
    """Store-wide aggregate counts for the Overview page."""

    evaluator_count: int
    dataset_count: int
    run_count: int
    total_rows: int
    labeled_rows: int
    best_accuracy: float | None
    latest_run: LatestRun | None


def _read_accuracy(metrics: dict | None) -> float | None:
    """Read ``metrics["accuracy"]`` as a float, or ``None`` for any unusable shape.

    ``Run.metrics`` is an untyped JSON dict: a run may carry ``None``, a dict without the
    key, or a non-numeric value. Each of those means "no accuracy" rather than an error, so
    none may raise. ``bool`` is rejected explicitly because it is a subclass of ``int`` and
    an accuracy of ``True`` would otherwise read as ``1.0``.
    """
    if not isinstance(metrics, dict):
        return None
    value = metrics.get("accuracy")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _as_utc(moment: datetime | None) -> datetime | None:
    """Tag a naive timestamp as UTC, since SQLite drops the tzinfo everything is stored with."""
    if moment is None:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


class Store:
    """Synchronous CRUD access to valcore entities backed by SQLite."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # -- Evaluators -----------------------------------------------------------

    def create_evaluator(self, name: str, description: str = "") -> Evaluator:
        """Create and persist a new evaluator."""
        with session_scope(self.engine) as session:
            evaluator = Evaluator(name=name, description=description)
            session.add(evaluator)
            return evaluator

    def get_evaluator(self, id: str) -> Evaluator:
        """Return the evaluator with ``id`` or raise NotFoundError."""
        with session_scope(self.engine) as session:
            return _require(session, Evaluator, id)

    def list_evaluators(self) -> list[Evaluator]:
        """Return every evaluator ordered by creation time."""
        with session_scope(self.engine) as session:
            return list(session.exec(select(Evaluator).order_by(Evaluator.created_at)))

    def update_evaluator(self, id: str, **fields: object) -> Evaluator:
        """Update mutable fields on an evaluator."""
        with session_scope(self.engine) as session:
            evaluator = _require(session, Evaluator, id)
            for key, value in fields.items():
                setattr(evaluator, key, value)
            session.add(evaluator)
            return evaluator

    def delete_evaluator(self, id: str) -> None:
        """Delete an evaluator and all of its versions, unless runs reference it."""
        with session_scope(self.engine) as session:
            evaluator = _require(session, Evaluator, id)
            version_ids = list(
                session.exec(select(EvaluatorVersion.id).where(EvaluatorVersion.evaluator_id == id))
            )
            runs = (
                session.exec(select(Run).where(Run.version_id.in_(version_ids))).all()
                if version_ids
                else []
            )
            if runs:
                _raise_referenced(runs, "evaluator")
            versions = session.exec(
                select(EvaluatorVersion).where(EvaluatorVersion.evaluator_id == id)
            )
            for version in versions:
                session.delete(version)
            session.delete(evaluator)

    # -- Versions -------------------------------------------------------------

    def create_version(self, evaluator_id: str, **fields: object) -> EvaluatorVersion:
        """Validate and persist a new version, making it the evaluator's active one."""
        with session_scope(self.engine) as session:
            evaluator = _require(session, Evaluator, evaluator_id)
            version = EvaluatorVersion(evaluator_id=evaluator_id, **fields)
            validate_version(version)
            session.add(version)
            session.flush()
            evaluator.active_version_id = version.id
            session.add(evaluator)
            return version

    def get_version(self, id: str) -> EvaluatorVersion:
        """Return the version with ``id`` or raise NotFoundError."""
        with session_scope(self.engine) as session:
            return _require(session, EvaluatorVersion, id)

    def list_versions(self, evaluator_id: str) -> list[EvaluatorVersion]:
        """Return every version of an evaluator ordered by creation time."""
        with session_scope(self.engine) as session:
            return list(
                session.exec(
                    select(EvaluatorVersion)
                    .where(EvaluatorVersion.evaluator_id == evaluator_id)
                    .order_by(EvaluatorVersion.created_at)
                )
            )

    def update_version(self, id: str, **fields: object) -> EvaluatorVersion:
        """Update mutable fields on a version, refusing if it is frozen."""
        with session_scope(self.engine) as session:
            version = _require(session, EvaluatorVersion, id)
            if version.frozen:
                raise FrozenVersionError(f"Version {id!r} is frozen and cannot be edited.")
            for key, value in fields.items():
                setattr(version, key, value)
            session.add(version)
            return version

    def freeze_version(self, id: str) -> EvaluatorVersion:
        """Mark a version frozen so it can no longer be edited."""
        with session_scope(self.engine) as session:
            version = _require(session, EvaluatorVersion, id)
            version.frozen = True
            session.add(version)
            return version

    def delete_version(self, id: str) -> None:
        """Delete a version, repointing the evaluator's active version if needed."""
        with session_scope(self.engine) as session:
            version = _require(session, EvaluatorVersion, id)
            runs = session.exec(select(Run).where(Run.version_id == id)).all()
            if runs:
                _raise_referenced(runs, "version")
            evaluator = session.get(Evaluator, version.evaluator_id)
            session.delete(version)
            session.flush()
            if evaluator is not None and evaluator.active_version_id == id:
                survivor = session.exec(
                    select(EvaluatorVersion)
                    .where(EvaluatorVersion.evaluator_id == evaluator.id)
                    .order_by(EvaluatorVersion.created_at.desc())
                ).first()
                evaluator.active_version_id = survivor.id if survivor is not None else None
                session.add(evaluator)

    # -- Agents ---------------------------------------------------------------

    def create_agent(self, name: str, description: str = "") -> Agent:
        """Create and persist a new agent under test."""
        with session_scope(self.engine) as session:
            agent = Agent(name=name, description=description)
            session.add(agent)
            return agent

    def get_agent(self, id: str) -> Agent:
        """Return the agent with ``id`` or raise NotFoundError."""
        with session_scope(self.engine) as session:
            return _require(session, Agent, id)

    def list_agents(self) -> list[Agent]:
        """Return every agent ordered by creation time."""
        with session_scope(self.engine) as session:
            return list(session.exec(select(Agent).order_by(Agent.created_at)))

    def update_agent(self, id: str, **fields: object) -> Agent:
        """Update mutable fields on an agent."""
        with session_scope(self.engine) as session:
            agent = _require(session, Agent, id)
            for key, value in fields.items():
                setattr(agent, key, value)
            session.add(agent)
            return agent

    def delete_agent(self, id: str) -> None:
        """Delete an agent and its versions unless persisted work references one."""
        with session_scope(self.engine) as session:
            agent = _require(session, Agent, id)
            version_ids = list(
                session.exec(select(AgentVersion.id).where(AgentVersion.agent_id == id))
            )
            derivations = (
                session.exec(
                    select(DatasetDerivation).where(
                        DatasetDerivation.agent_version_id.in_(version_ids)
                    )
                ).all()
                if version_ids
                else []
            )
            if derivations:
                _raise_referenced_by_derivations(derivations, "agent")
            runs = (
                session.exec(
                    select(Run).where(
                        Run.kind == RunKind.DERIVE,
                        Run.version_id.in_(version_ids),
                    )
                ).all()
                if version_ids
                else []
            )
            if runs:
                _raise_referenced(runs, "agent")
            link = session.exec(
                select(AgentPromptSyncLink).where(AgentPromptSyncLink.agent_id == id)
            ).first()
            if link is not None:
                session.delete(link)
            versions = session.exec(select(AgentVersion).where(AgentVersion.agent_id == id))
            for version in versions:
                session.delete(version)
            session.delete(agent)

    def create_agent_version(self, agent_id: str, **fields: object) -> AgentVersion:
        """Validate and persist a new agent version, making it active."""
        with session_scope(self.engine) as session:
            agent = _require(session, Agent, agent_id)
            version = AgentVersion(agent_id=agent_id, **fields)
            validate_agent_version(version)
            session.add(version)
            session.flush()
            agent.active_version_id = version.id
            session.add(agent)
            return version

    def get_agent_version(self, id: str) -> AgentVersion:
        """Return the agent version with ``id`` or raise NotFoundError."""
        with session_scope(self.engine) as session:
            return _require(session, AgentVersion, id)

    def list_agent_versions(self, agent_id: str) -> list[AgentVersion]:
        """Return every version of an agent ordered by creation time."""
        with session_scope(self.engine) as session:
            return list(
                session.exec(
                    select(AgentVersion)
                    .where(AgentVersion.agent_id == agent_id)
                    .order_by(AgentVersion.created_at)
                )
            )

    def update_agent_version(self, id: str, **fields: object) -> AgentVersion:
        """Update an unfrozen agent version after validating its complete binding."""
        with session_scope(self.engine) as session:
            version = _require(session, AgentVersion, id)
            if version.frozen:
                raise FrozenVersionError(f"Agent version {id!r} is frozen and cannot be edited.")
            for key, value in fields.items():
                setattr(version, key, value)
            validate_agent_version(version)
            session.add(version)
            return version

    def freeze_agent_version(self, id: str) -> AgentVersion:
        """Mark an agent version frozen so it can no longer be edited."""
        with session_scope(self.engine) as session:
            version = _require(session, AgentVersion, id)
            version.frozen = True
            session.add(version)
            return version

    def delete_agent_version(self, id: str) -> None:
        """Delete an agent version, repointing its active version when needed."""
        with session_scope(self.engine) as session:
            version = _require(session, AgentVersion, id)
            derivations = session.exec(
                select(DatasetDerivation).where(DatasetDerivation.agent_version_id == id)
            ).all()
            if derivations:
                _raise_referenced_by_derivations(derivations, "agent version")
            runs = session.exec(
                select(Run).where(Run.kind == RunKind.DERIVE, Run.version_id == id)
            ).all()
            if runs:
                _raise_referenced(runs, "agent version")
            agent = session.get(Agent, version.agent_id)
            session.delete(version)
            session.flush()
            if agent is not None and agent.active_version_id == id:
                survivor = session.exec(
                    select(AgentVersion)
                    .where(AgentVersion.agent_id == agent.id)
                    .order_by(AgentVersion.created_at.desc())
                ).first()
                agent.active_version_id = survivor.id if survivor is not None else None
                session.add(agent)

    def copy_agent_version(self, id: str, version_name: str) -> AgentVersion:
        """Copy an agent version into a new, unfrozen active version."""
        with session_scope(self.engine) as session:
            source = _require(session, AgentVersion, id)
            agent = _require(session, Agent, source.agent_id)
            version = AgentVersion(
                agent_id=source.agent_id,
                version_name=version_name,
                notes=source.notes,
                frozen=False,
                model=source.model,
                spec=source.spec,
                prompt_template=source.prompt_template,
                required_columns=source.required_columns,
                deps_mapping=source.deps_mapping,
            )
            validate_agent_version(version)
            session.add(version)
            session.flush()
            agent.active_version_id = version.id
            session.add(agent)
            return version

    # -- Agent prompt sync links --------------------------------------------

    def get_agent_prompt_sync_link(self, agent_id: str) -> AgentPromptSyncLink | None:
        """Return the local prompt sync cursor, if the agent is linked."""
        with session_scope(self.engine) as session:
            return session.exec(
                select(AgentPromptSyncLink).where(AgentPromptSyncLink.agent_id == agent_id)
            ).first()

    def create_agent_prompt_sync_link(
        self,
        agent_id: str,
        *,
        key_fingerprint: str,
        instructions_variable_name: str,
        input_template_variable_name: str,
        instructions_remote_version: int | None,
        input_template_remote_version: int | None,
        instructions_base_text: str,
        input_template_base_text: str,
        expected_active_version_id: str | None,
        expected_local_texts: dict[str, str],
        initial_version_fields: dict[str, object] | None = None,
    ) -> AgentPromptSyncLink:
        """Link an agent, optionally creating its first remotely sourced version atomically."""
        with session_scope(self.engine) as session:
            _begin_prompt_sync_write(session)
            agent = _require(session, Agent, agent_id)
            _check_prompt_sync_local_state(
                session, agent, expected_active_version_id, expected_local_texts
            )
            if (
                session.exec(
                    select(AgentPromptSyncLink.id).where(AgentPromptSyncLink.agent_id == agent_id)
                ).first()
                is not None
            ):
                raise SyncConflictError("The agent is already linked; inspect sync again.")
            version_id = agent.active_version_id
            if initial_version_fields is not None:
                version = AgentVersion(agent_id=agent_id, **initial_version_fields)
                validate_agent_version(version)
                session.add(version)
                session.flush()
                version_id = version.id
                agent.active_version_id = version_id
                session.add(agent)
            link = AgentPromptSyncLink(
                agent_id=agent_id,
                key_fingerprint=key_fingerprint,
                agent_version_id=version_id,
                instructions_variable_name=instructions_variable_name,
                input_template_variable_name=input_template_variable_name,
                instructions_remote_version=instructions_remote_version,
                input_template_remote_version=input_template_remote_version,
                instructions_base_text=instructions_base_text,
                input_template_base_text=input_template_base_text,
            )
            session.add(link)
            session.flush()
            return link

    def advance_agent_prompt_sync_link(
        self,
        agent_id: str,
        *,
        expected_link_id: str,
        expected_generation: int,
        field_updates: dict[str, dict[str, object]],
        expected_active_version_id: str | None = None,
        expected_local_texts: dict[str, str] | None = None,
    ) -> AgentPromptSyncLink:
        """Advance selected cursor fields on the inspected link.

        A Push also supplies the active version and both inspected texts, so its
        successful cursor advance records the local version that was published.
        """
        if (expected_active_version_id is None) != (expected_local_texts is None):
            raise ValueError("Push cursor advances require both active version and local texts.")
        with session_scope(self.engine) as session:
            _begin_prompt_sync_write(session)
            if expected_local_texts is not None:
                agent = _require(session, Agent, agent_id)
                _check_prompt_sync_local_state(
                    session, agent, expected_active_version_id, expected_local_texts
                )
            return _advance_prompt_sync_cursor(
                session,
                agent_id,
                expected_link_id,
                expected_generation,
                field_updates,
                agent_version_id=expected_active_version_id,
            )

    def create_agent_version_and_advance_prompt_sync_link(
        self,
        agent_id: str,
        *,
        expected_link_id: str,
        expected_active_version_id: str | None,
        expected_local_texts: dict[str, str],
        expected_generation: int,
        version_fields: dict[str, object],
        field_updates: dict[str, dict[str, object]],
    ) -> AgentVersion:
        """Validate and activate a pulled version with a cursor advance in one transaction."""
        with session_scope(self.engine) as session:
            _begin_prompt_sync_write(session)
            agent = _require(session, Agent, agent_id)
            _check_prompt_sync_local_state(
                session, agent, expected_active_version_id, expected_local_texts
            )
            version = AgentVersion(agent_id=agent_id, **version_fields)
            validate_agent_version(version)
            session.add(version)
            session.flush()
            agent.active_version_id = version.id
            session.add(agent)
            _advance_prompt_sync_cursor(
                session,
                agent_id,
                expected_link_id,
                expected_generation,
                field_updates,
                agent_version_id=version.id,
            )
            session.refresh(version)
            return version

    def delete_agent_prompt_sync_link(
        self, agent_id: str, *, expected_link_id: str, expected_generation: int
    ) -> None:
        """Unlink only the inspected cursor at its inspected generation."""
        with session_scope(self.engine) as session:
            result = session.exec(
                delete(AgentPromptSyncLink).where(
                    AgentPromptSyncLink.agent_id == agent_id,
                    AgentPromptSyncLink.id == expected_link_id,
                    AgentPromptSyncLink.generation == expected_generation,
                )
            )
            if result.rowcount != 1:
                raise SyncConflictError("The prompt sync cursor changed; inspect sync again.")

    # -- Derivations ----------------------------------------------------------

    def save_derivation(
        self,
        *,
        dataset_id: str,
        agent_version_id: str,
        response_columns: list[str],
        responses: list[dict],
    ) -> DatasetDerivation:
        """Persist one complete agent-response overlay for a dataset."""
        if not responses:
            raise ContractError("A derivation must contain at least one response.")
        for response in responses:
            if "row_id" not in response:
                raise ContractError("Derivation response is missing required key 'row_id'.")
            if not isinstance(response["row_id"], str):
                raise ContractError("Derivation response key 'row_id' must be a string.")
            if "data" not in response:
                raise ContractError("Derivation response is missing required key 'data'.")
            if not isinstance(response["data"], dict):
                raise ContractError("Derivation response key 'data' must be a dict.")
        with session_scope(self.engine) as session:
            _require(session, Dataset, dataset_id)
            _require(session, AgentVersion, agent_version_id)
            row_ids = [response["row_id"] for response in responses]
            seen_row_ids: set[str] = set()
            duplicate_row_ids: set[str] = set()
            for row_id in row_ids:
                if row_id in seen_row_ids:
                    duplicate_row_ids.add(row_id)
                seen_row_ids.add(row_id)
            if duplicate_row_ids:
                raise ContractError(
                    "Derivation responses contain duplicate row_id values: "
                    f"{sorted(duplicate_row_ids)}."
                )

            # Responses are overlays on one declared dataset, so an orphan or foreign row
            # would make the derived view silently incomplete or join unrelated input data.
            source_rows = session.exec(select(DatasetRow).where(DatasetRow.id.in_(row_ids))).all()
            rows_by_id = {row.id: row for row in source_rows}
            missing_row_ids = [row_id for row_id in row_ids if row_id not in rows_by_id]
            if missing_row_ids:
                raise ContractError(
                    f"Derivation responses reference missing row_id values: {missing_row_ids}."
                )
            wrong_dataset_row_ids = [
                row_id for row_id in row_ids if rows_by_id[row_id].dataset_id != dataset_id
            ]
            if wrong_dataset_row_ids:
                raise ContractError(
                    "Derivation responses reference rows outside dataset "
                    f"{dataset_id!r}: {wrong_dataset_row_ids}."
                )

            current_max = self._saved_derivation_max_ordinal(session, dataset_id)
            derivation = DatasetDerivation(
                dataset_id=dataset_id,
                agent_version_id=agent_version_id,
                ordinal=0 if current_max is None else current_max + 1,
                response_columns=response_columns,
            )
            session.add(derivation)
            session.flush()
            session.add(DerivationStatus(derivation_id=derivation.id, state=DerivationState.SAVED))
            for response in responses:
                session.add(
                    AgentResponse(
                        derivation_id=derivation.id,
                        dataset_row_id=response["row_id"],
                        data=response["data"],
                        latency_ms=response.get("latency_ms"),
                        usage=response.get("usage"),
                        error=response.get("error"),
                    )
                )
            return derivation

    @staticmethod
    def _saved_derivation_max_ordinal(session: Session, dataset_id: str) -> int | None:
        """Return the largest ordinal assigned to a saved derivation of a dataset."""
        return session.exec(
            select(func.max(DatasetDerivation.ordinal))
            .outerjoin(
                DerivationStatus,
                DerivationStatus.derivation_id == DatasetDerivation.id,
            )
            .where(
                DatasetDerivation.dataset_id == dataset_id,
                or_(
                    DerivationStatus.state == DerivationState.SAVED,
                    DerivationStatus.id.is_(None),
                ),
            )
        ).one()

    def create_staged_derivation(
        self,
        *,
        dataset_id: str,
        agent_version_id: str,
        response_columns: list[str],
    ) -> DatasetDerivation:
        """Create an empty derivation whose ordinal remains unallocated while staged."""
        with session_scope(self.engine) as session:
            _require(session, Dataset, dataset_id)
            _require(session, AgentVersion, agent_version_id)
            derivation = DatasetDerivation(
                dataset_id=dataset_id,
                agent_version_id=agent_version_id,
                response_columns=response_columns,
            )
            session.add(derivation)
            session.flush()
            session.add(DerivationStatus(derivation_id=derivation.id, state=DerivationState.STAGED))
            return derivation

    def add_agent_response(self, derivation_id: str, response: dict) -> AgentResponse:
        """Append one completed agent response using an isolated session for each worker."""
        if "row_id" not in response or not isinstance(response["row_id"], str):
            raise ContractError("Derivation response key 'row_id' must be a string.")
        if "data" not in response or not isinstance(response["data"], dict):
            raise ContractError("Derivation response key 'data' must be a dict.")
        with session_scope(self.engine) as session:
            derivation = _require(session, DatasetDerivation, derivation_id)
            status = session.exec(
                select(DerivationStatus).where(DerivationStatus.derivation_id == derivation_id)
            ).first()
            if status is None or status.state is not DerivationState.STAGED:
                raise ContractError(
                    f"Derivation {derivation_id!r} is saved and cannot accept responses."
                )
            row = _require(session, DatasetRow, response["row_id"])
            if row.dataset_id != derivation.dataset_id:
                raise ContractError(
                    f"Derivation response row {row.id!r} is outside dataset "
                    f"{derivation.dataset_id!r}."
                )
            agent_response = AgentResponse(
                derivation_id=derivation_id,
                dataset_row_id=row.id,
                data=response["data"],
                latency_ms=response.get("latency_ms"),
                usage=response.get("usage"),
                error=response.get("error"),
            )
            session.add(agent_response)
            return agent_response

    def save_staged_derivation(self, derivation_id: str) -> DatasetDerivation:
        """Accept a staged derivation and allocate its next saved ordinal."""
        with session_scope(self.engine) as session:
            derivation = _require(session, DatasetDerivation, derivation_id)
            status = session.exec(
                select(DerivationStatus).where(DerivationStatus.derivation_id == derivation_id)
            ).first()
            if status is None or status.state is DerivationState.SAVED:
                raise ContractError(f"Derivation {derivation_id!r} is already saved.")
            current_max = self._saved_derivation_max_ordinal(session, derivation.dataset_id)
            derivation.ordinal = 0 if current_max is None else current_max + 1
            status.state = DerivationState.SAVED
            session.add(derivation)
            session.add(status)
            return derivation

    def delete_derivation(self, derivation_id: str) -> None:
        """Delete a discardable derivation and its dependent response and state records."""
        with session_scope(self.engine) as session:
            _require(session, DatasetDerivation, derivation_id)
            links = session.exec(
                select(RunDerivation).where(RunDerivation.derivation_id == derivation_id)
            ).all()
            if any(link.role is DerivationRole.READS for link in links):
                raise ReferencedError(f"Derivation {derivation_id!r} is referenced by a run.")
            for link in links:
                session.delete(link)
            for response in session.exec(
                select(AgentResponse).where(AgentResponse.derivation_id == derivation_id)
            ):
                session.delete(response)
            status = session.exec(
                select(DerivationStatus).where(DerivationStatus.derivation_id == derivation_id)
            ).first()
            if status is not None:
                session.delete(status)
            session.delete(_require(session, DatasetDerivation, derivation_id))

    def derivation_state(self, derivation_id: str) -> DerivationState:
        """Return a derivation's persisted state, treating legacy rows as saved."""
        with session_scope(self.engine) as session:
            _require(session, DatasetDerivation, derivation_id)
            status = session.exec(
                select(DerivationStatus).where(DerivationStatus.derivation_id == derivation_id)
            ).first()
            return DerivationState.SAVED if status is None else status.state

    def get_derivation(self, id: str) -> DatasetDerivation:
        """Return the derivation with ``id`` or raise NotFoundError."""
        with session_scope(self.engine) as session:
            return _require(session, DatasetDerivation, id)

    def list_derivations(
        self,
        *,
        dataset_id: str | None = None,
        agent_version_id: str | None = None,
        include_staged: bool = False,
    ) -> list[DatasetDerivation]:
        """Return derivations filtered by their optional dataset and agent version bindings."""
        with session_scope(self.engine) as session:
            statement = select(DatasetDerivation)
            if dataset_id is not None:
                statement = statement.where(DatasetDerivation.dataset_id == dataset_id)
            if agent_version_id is not None:
                statement = statement.where(DatasetDerivation.agent_version_id == agent_version_id)
            if not include_staged:
                statement = statement.outerjoin(
                    DerivationStatus,
                    DerivationStatus.derivation_id == DatasetDerivation.id,
                ).where(
                    or_(
                        DerivationStatus.state == DerivationState.SAVED,
                        DerivationStatus.id.is_(None),
                    )
                )
            return list(session.exec(statement.order_by(DatasetDerivation.created_at)))

    def list_agent_responses(self, derivation_id: str) -> list[AgentResponse]:
        """Return every response saved for a derivation."""
        with session_scope(self.engine) as session:
            _require(session, DatasetDerivation, derivation_id)
            return list(
                session.exec(
                    select(AgentResponse).where(AgentResponse.derivation_id == derivation_id)
                )
            )

    def derived_rows(self, derivation_id: str, *, include_errors: bool = True) -> list[DerivedRow]:
        """Return the dataset inputs overlaid with a derivation's responses by row index."""
        with session_scope(self.engine) as session:
            _require(session, DatasetDerivation, derivation_id)
            response_statement = select(AgentResponse).where(
                AgentResponse.derivation_id == derivation_id
            )
            if not include_errors:
                response_statement = response_statement.where(AgentResponse.error.is_(None))
            responses = session.exec(response_statement).all()
            row_ids = [response.dataset_row_id for response in responses]
            rows = (
                session.exec(select(DatasetRow).where(DatasetRow.id.in_(row_ids))).all()
                if row_ids
                else []
            )
            rows_by_id = {row.id: row for row in rows}
            joined = []
            for response in responses:
                row = rows_by_id.get(response.dataset_row_id)
                if row is None:
                    continue
                joined.append(
                    DerivedRow(
                        row_id=row.id,
                        idx=row.idx,
                        data={**row.data, **response.data},
                        latency_ms=response.latency_ms,
                        error=response.error,
                    )
                )
            return sorted(joined, key=lambda row: row.idx)

    # -- Datasets -------------------------------------------------------------

    def create_dataset(
        self,
        name: str,
        description: str,
        columns: list[str],
    ) -> Dataset:
        """Create and persist a new dataset."""
        with session_scope(self.engine) as session:
            dataset = Dataset(
                name=name,
                description=description,
                columns=columns,
            )
            session.add(dataset)
            return dataset

    def get_dataset(self, id: str) -> Dataset:
        """Return the dataset with ``id`` or raise NotFoundError."""
        with session_scope(self.engine) as session:
            return _require(session, Dataset, id)

    def list_datasets(self) -> list[DatasetSummary]:
        """Return every dataset ordered by creation time, each with its row/label counts.

        ``labeled_count`` comes from the dataset's primary (oldest) label set's annotations
        -- a different table from the rows themselves, so unlike the row count it cannot be
        filled by one grouped join and is resolved per dataset instead.
        """
        with session_scope(self.engine) as session:
            datasets = list(session.exec(select(Dataset).order_by(Dataset.created_at)))
            row_counts = dict(
                session.exec(
                    select(DatasetRow.dataset_id, func.count(DatasetRow.id)).group_by(
                        DatasetRow.dataset_id
                    )
                ).all()
            )
            label_sets_by_dataset: dict[str, LabelSet] = {}
            for label_set in session.exec(select(LabelSet).order_by(LabelSet.created_at)):
                label_sets_by_dataset.setdefault(label_set.dataset_id, label_set)
            summaries = []
            for dataset in datasets:
                row_count = row_counts.get(dataset.id, 0)
                primary = label_sets_by_dataset.get(dataset.id)
                labeled_count = 0
                if primary is not None:
                    primary_annotations = session.exec(
                        select(Annotation).where(Annotation.label_set_id == primary.id)
                    ).all()
                    labeled_count = sum(
                        1
                        for annotation in primary_annotations
                        if annotation_ground_truth(primary, annotation) is not None
                    )
                summaries.append(
                    DatasetSummary(
                        id=dataset.id,
                        created_at=dataset.created_at,
                        name=dataset.name,
                        description=dataset.description,
                        columns=dataset.columns,
                        row_count=row_count,
                        labeled_count=labeled_count,
                    )
                )
            return summaries

    def update_dataset(
        self,
        id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        columns: list[str] | None = None,
        column_renames: dict[str, str] | None = None,
    ) -> Dataset:
        """Update a dataset's metadata and shape, migrating its rows."""
        with session_scope(self.engine) as session:
            dataset = _require(session, Dataset, id)
            if name is not None:
                dataset.name = name
            if description is not None:
                dataset.description = description

            renames = column_renames or {}
            for key in renames:
                if key not in dataset.columns:
                    raise ContractError(
                        f"Cannot rename unknown column {key!r}.",
                        detail={"column": key},
                    )

            if columns is not None or column_renames is not None:
                final_columns = (
                    columns
                    if columns is not None
                    else [renames.get(col, col) for col in dataset.columns]
                )
                rows = session.exec(select(DatasetRow).where(DatasetRow.dataset_id == id)).all()
                for row in rows:
                    row.data = apply_column_changes(row.data, renames, final_columns)
                    session.add(row)
                dataset.columns = final_columns

            session.add(dataset)
            return dataset

    def delete_dataset(self, id: str) -> None:
        """Delete a dataset and all of its rows, unless runs reference it."""
        with session_scope(self.engine) as session:
            dataset = _require(session, Dataset, id)
            runs = session.exec(select(Run).where(Run.dataset_id == id)).all()
            if runs:
                _raise_referenced(runs, "dataset")
            rows = session.exec(select(DatasetRow).where(DatasetRow.dataset_id == id))
            for row in rows:
                session.delete(row)
            # The annotation records have no meaning without their label set; leaving them would
            # orphan rows that nothing can reach. The label set has no meaning without its dataset;
            # leaving it would orphan rows that nothing can reach.
            label_sets = session.exec(select(LabelSet).where(LabelSet.dataset_id == id))
            for label_set in label_sets:
                annotations = session.exec(
                    select(Annotation).where(Annotation.label_set_id == label_set.id)
                )
                for annotation in annotations:
                    session.delete(annotation)
                session.delete(label_set)
            # The generation record has no meaning without its dataset; leaving it would
            # orphan a row that nothing can reach.
            generations = session.exec(
                select(DatasetGeneration).where(DatasetGeneration.dataset_id == id)
            )
            for generation in generations:
                session.delete(generation)
            pulls = session.exec(
                select(DatasetLogfirePull).where(DatasetLogfirePull.dataset_id == id)
            )
            for pull in pulls:
                session.delete(pull)
            hosted_fetches = session.exec(
                select(DatasetHostedFetch).where(DatasetHostedFetch.dataset_id == id)
            )
            for fetch in hosted_fetches:
                session.delete(fetch)
            session.delete(dataset)

    def add_rows(self, dataset_id: str, rows: list[dict]) -> list[DatasetRow]:
        """Append data-only rows, assigning sequential ``idx`` from the current max."""
        return self.add_prepared_rows(dataset_id, [{"data": data} for data in rows])

    def add_prepared_rows(self, dataset_id: str, rows: list[dict]) -> list[DatasetRow]:
        """Append rows given as full ``DatasetRow`` field dicts (each must include ``data``)."""
        with session_scope(self.engine) as session:
            _require(session, Dataset, dataset_id)
            current_max = session.exec(
                select(func.max(DatasetRow.idx)).where(DatasetRow.dataset_id == dataset_id)
            ).one()
            next_idx = 0 if current_max is None else current_max + 1
            created: list[DatasetRow] = []
            for offset, fields in enumerate(rows):
                row = DatasetRow(dataset_id=dataset_id, idx=next_idx + offset, **fields)
                session.add(row)
                created.append(row)
            return created

    def set_generation(self, dataset_id: str, **fields) -> DatasetGeneration:
        """Record (or replace) how ``dataset_id``'s rows were generated.

        Replaces rather than accumulates: the settings describe the most recent ask, which
        is what a form should be repopulated from. A dataset therefore has at most one row.
        """
        with session_scope(self.engine) as session:
            _require(session, Dataset, dataset_id)
            existing = session.exec(
                select(DatasetGeneration).where(DatasetGeneration.dataset_id == dataset_id)
            ).first()
            if existing is not None:
                session.delete(existing)
                session.flush()
            generation = DatasetGeneration(dataset_id=dataset_id, **fields)
            session.add(generation)
            return generation

    def get_generation(self, dataset_id: str) -> DatasetGeneration | None:
        """Return how ``dataset_id`` was generated, or None for an uploaded or blank dataset."""
        with session_scope(self.engine) as session:
            _require(session, Dataset, dataset_id)
            return session.exec(
                select(DatasetGeneration).where(DatasetGeneration.dataset_id == dataset_id)
            ).first()

    def set_logfire_pull(self, dataset_id: str, **fields) -> DatasetLogfirePull:
        """Record (or replace) how ``dataset_id``'s rows were pulled from Logfire."""
        with session_scope(self.engine) as session:
            _require(session, Dataset, dataset_id)
            existing = session.exec(
                select(DatasetLogfirePull).where(DatasetLogfirePull.dataset_id == dataset_id)
            ).first()
            if existing is not None:
                session.delete(existing)
                session.flush()
            pull = DatasetLogfirePull(dataset_id=dataset_id, **fields)
            session.add(pull)
            return pull

    def get_logfire_pull(self, dataset_id: str) -> DatasetLogfirePull | None:
        """Return how ``dataset_id`` was pulled from Logfire, or None if it was not."""
        with session_scope(self.engine) as session:
            _require(session, Dataset, dataset_id)
            return session.exec(
                select(DatasetLogfirePull).where(DatasetLogfirePull.dataset_id == dataset_id)
            ).first()

    def set_hosted_fetch(self, dataset_id: str, **fields) -> DatasetHostedFetch:
        """Record (or replace) which hosted dataset ``dataset_id`` was fetched from."""
        with session_scope(self.engine) as session:
            _require(session, Dataset, dataset_id)
            existing = session.exec(
                select(DatasetHostedFetch).where(DatasetHostedFetch.dataset_id == dataset_id)
            ).first()
            if existing is not None:
                session.delete(existing)
                session.flush()
            fetch = DatasetHostedFetch(dataset_id=dataset_id, **fields)
            session.add(fetch)
            return fetch

    def get_hosted_fetch(self, dataset_id: str) -> DatasetHostedFetch | None:
        """Return which hosted dataset ``dataset_id`` was fetched from, or None if it was not."""
        with session_scope(self.engine) as session:
            _require(session, Dataset, dataset_id)
            return session.exec(
                select(DatasetHostedFetch).where(DatasetHostedFetch.dataset_id == dataset_id)
            ).first()

    def get_row(self, id: str) -> DatasetRow:
        """Return the dataset row with ``id`` or raise NotFoundError."""
        with session_scope(self.engine) as session:
            return _require(session, DatasetRow, id)

    def list_rows(
        self, dataset_id: str, limit: int | None = None, offset: int = 0
    ) -> list[DatasetRow]:
        """Return rows of a dataset ordered by ``idx``, optionally paginated."""
        with session_scope(self.engine) as session:
            query = (
                select(DatasetRow)
                .where(DatasetRow.dataset_id == dataset_id)
                .order_by(DatasetRow.idx)
                .offset(offset)
            )
            if limit is not None:
                query = query.limit(limit)
            return list(session.exec(query))

    def update_row(self, id: str, **fields: object) -> DatasetRow:
        """Update the given fields on a dataset row."""
        with session_scope(self.engine) as session:
            row = _require(session, DatasetRow, id)
            for key, value in fields.items():
                setattr(row, key, value)
            session.add(row)
            return row

    def delete_row(self, id: str) -> None:
        """Delete a single dataset row and all its annotations."""
        with session_scope(self.engine) as session:
            row = _require(session, DatasetRow, id)
            # The annotation records have no meaning without their dataset row; leaving them would
            # orphan rows that nothing can reach.
            annotations = session.exec(select(Annotation).where(Annotation.dataset_row_id == id))
            for annotation in annotations:
                session.delete(annotation)
            session.delete(row)

    # -- Label sets -------------------------------------------------------------

    def create_label_set(
        self,
        dataset_id: str,
        name: str,
        description: str,
        kind: ScoreKind,
        labels: list[dict] | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> LabelSet:
        """Validate and persist a new label set (annotation contract) for a dataset."""
        with session_scope(self.engine) as session:
            _require(session, Dataset, dataset_id)
            label_set = LabelSet(
                dataset_id=dataset_id,
                name=name,
                description=description,
                kind=kind,
                labels=labels,
                minimum=minimum,
                maximum=maximum,
            )
            validate_label_set(label_set)
            session.add(label_set)
            return label_set

    def get_label_set(self, id: str) -> LabelSet:
        """Return the label set with ``id`` or raise NotFoundError."""
        with session_scope(self.engine) as session:
            return _require(session, LabelSet, id)

    def list_label_sets(self, dataset_id: str) -> list[LabelSet]:
        """Return every label set of a dataset ordered by creation time."""
        with session_scope(self.engine) as session:
            return list(
                session.exec(
                    select(LabelSet)
                    .where(LabelSet.dataset_id == dataset_id)
                    .order_by(LabelSet.created_at)
                )
            )

    def primary_label_set(self, dataset_id: str) -> LabelSet | None:
        """Return a dataset's oldest label set, or None if it has none.

        Generation, upload, and Logfire-pull flows create at most one label set per
        dataset, so "oldest" is unambiguous for every dataset those flows produced. A
        dataset with additional hand-authored label sets still resolves to the one from
        its original creation flow, which is what continued generation and legacy
        stats/labeled-count reporting track.
        """
        with session_scope(self.engine) as session:
            _require(session, Dataset, dataset_id)
            return session.exec(
                select(LabelSet)
                .where(LabelSet.dataset_id == dataset_id)
                .order_by(LabelSet.created_at)
            ).first()

    def update_label_set(
        self, id: str, *, name: str | None = None, description: str | None = None
    ) -> LabelSet:
        """Rename or redescribe a label set; its label space is fixed at creation."""
        with session_scope(self.engine) as session:
            label_set = _require(session, LabelSet, id)
            if name is not None:
                label_set.name = name
            if description is not None:
                label_set.description = description
            session.add(label_set)
            return label_set

    def delete_label_set(self, id: str) -> None:
        """Delete a label set and every annotation recorded against it."""
        with session_scope(self.engine) as session:
            label_set = _require(session, LabelSet, id)
            annotations = session.exec(select(Annotation).where(Annotation.label_set_id == id))
            for annotation in annotations:
                session.delete(annotation)
            session.delete(label_set)

    # -- Annotations ----------------------------------------------------------

    def set_annotation(
        self,
        label_set_id: str,
        dataset_row_id: str,
        *,
        labels: list[str] | None = None,
        value: float | None = None,
        description: str | None = None,
        source: LabelSource | None = None,
        suggested_labels: list[str] | None = None,
        suggested_value: float | None = None,
        reasoning: str | None = None,
    ) -> Annotation:
        """Create or update the single annotation for (label_set_id, dataset_row_id).

        A full replace of whichever fields the caller passes, not a merge -- mirroring
        ``set_label``'s replace semantics for the equivalent single-label case today.
        ``labels``/``value`` are the confirmed ground truth; ``suggested_labels``/
        ``suggested_value``/``reasoning`` are provenance a caller (generation, today) can
        set without touching the confirmed fields, so a suggestion stays unconfirmed until
        a separate call sets ``labels``/``value`` -- exactly as ``DatasetRow.suggested_label``
        stayed distinct from ``DatasetRow.label`` until an explicit accept.
        """
        with session_scope(self.engine) as session:
            label_set = _require(session, LabelSet, label_set_id)
            _require(session, DatasetRow, dataset_row_id)
            validate_annotation(label_set, labels=labels, value=value)
            existing = session.exec(
                select(Annotation).where(
                    Annotation.label_set_id == label_set_id,
                    Annotation.dataset_row_id == dataset_row_id,
                )
            ).first()
            now = datetime.now(UTC)
            if existing is None:
                annotation = Annotation(
                    label_set_id=label_set_id,
                    dataset_row_id=dataset_row_id,
                    labels=labels or [],
                    value=value,
                    description=description,
                    source=source,
                    suggested_labels=suggested_labels,
                    suggested_value=suggested_value,
                    reasoning=reasoning,
                    updated_at=now,
                )
                session.add(annotation)
                return annotation
            existing.labels = labels or []
            existing.value = value
            existing.description = description
            existing.source = source
            if suggested_labels is not None:
                existing.suggested_labels = suggested_labels
            if suggested_value is not None:
                existing.suggested_value = suggested_value
            if reasoning is not None:
                existing.reasoning = reasoning
            existing.updated_at = now
            session.add(existing)
            return existing

    def accept_annotation_suggestion(self, label_set_id: str, dataset_row_id: str) -> Annotation:
        """Promote an annotation's suggested labels/value into its confirmed fields.

        Mirrors the pre-Annotation ``patch_row(accept_suggestion=True)`` flow: copies
        whatever generation wrote into ``suggested_labels``/``suggested_value`` onto
        ``labels``/``value``, marking the result ``LabelSource.ACCEPTED``. The suggestion
        itself is left in place -- accepting is not the same as clearing it. Raises
        ContractError if there is no annotation, or an annotation with nothing suggested,
        to accept.
        """
        with session_scope(self.engine) as session:
            label_set = _require(session, LabelSet, label_set_id)
            annotation = session.exec(
                select(Annotation).where(
                    Annotation.label_set_id == label_set_id,
                    Annotation.dataset_row_id == dataset_row_id,
                )
            ).first()
            has_suggestion = annotation is not None and (
                annotation.suggested_labels or annotation.suggested_value is not None
            )
            if not has_suggestion:
                raise ContractError("Annotation has no suggested labels/value to accept.")
            if label_set.kind is ScoreKind.CATEGORICAL:
                annotation.labels = annotation.suggested_labels or []
            else:
                annotation.value = annotation.suggested_value
            annotation.source = LabelSource.ACCEPTED
            annotation.updated_at = datetime.now(UTC)
            session.add(annotation)
            return annotation

    def get_annotation(self, label_set_id: str, dataset_row_id: str) -> Annotation | None:
        """Return the annotation for (label_set_id, dataset_row_id), or None if unset."""
        with session_scope(self.engine) as session:
            return session.exec(
                select(Annotation).where(
                    Annotation.label_set_id == label_set_id,
                    Annotation.dataset_row_id == dataset_row_id,
                )
            ).first()

    def clear_annotation(self, label_set_id: str, dataset_row_id: str) -> None:
        """Delete the annotation for (label_set_id, dataset_row_id), if any."""
        with session_scope(self.engine) as session:
            existing = session.exec(
                select(Annotation).where(
                    Annotation.label_set_id == label_set_id,
                    Annotation.dataset_row_id == dataset_row_id,
                )
            ).first()
            if existing is not None:
                session.delete(existing)

    def list_annotations_for_rows(self, label_set_id: str, row_ids: list[str]) -> list[Annotation]:
        """Return every annotation for label_set_id restricted to the given row ids."""
        if not row_ids:
            return []
        with session_scope(self.engine) as session:
            return list(
                session.exec(
                    select(Annotation).where(
                        Annotation.label_set_id == label_set_id,
                        Annotation.dataset_row_id.in_(row_ids),
                    )
                )
            )

    def annotation_progress(self, label_set_id: str) -> tuple[int, int]:
        """Return ``(annotated, total)`` row counts for a label set's dataset."""
        with session_scope(self.engine) as session:
            label_set = _require(session, LabelSet, label_set_id)
            total = session.exec(
                select(func.count())
                .select_from(DatasetRow)
                .where(DatasetRow.dataset_id == label_set.dataset_id)
            ).one()
            annotated = session.exec(
                select(func.count())
                .select_from(Annotation)
                .where(Annotation.label_set_id == label_set_id)
            ).one()
            return annotated, total

    # -- Runs -----------------------------------------------------------------

    def create_run(
        self,
        kind: RunKind,
        version_id: str,
        dataset_id: str,
        concurrency: int,
    ) -> Run:
        """Create a run, freezing its version in the same transaction."""
        with session_scope(self.engine) as session:
            version_type = AgentVersion if kind is RunKind.DERIVE else EvaluatorVersion
            version = _require(session, version_type, version_id)
            _require(session, Dataset, dataset_id)
            version.frozen = True
            session.add(version)
            run = Run(
                kind=kind,
                version_id=version_id,
                dataset_id=dataset_id,
                status=RunStatus.PENDING,
                concurrency=concurrency,
            )
            session.add(run)
            return run

    def link_run_derivation(
        self, run_id: str, derivation_id: str, role: DerivationRole
    ) -> RunDerivation:
        """Link a run to one valid derivation it fills or reads, according to its kind."""
        with session_scope(self.engine) as session:
            run = _require(session, Run, run_id)
            derivation = _require(session, DatasetDerivation, derivation_id)
            existing = session.exec(
                select(RunDerivation).where(RunDerivation.run_id == run_id)
            ).first()
            if existing is not None:
                raise ContractError(f"Run {run_id!r} already has a derivation link.")
            if run.kind is RunKind.VALIDATION:
                raise ContractError("Validation runs cannot read or fill derivations.")
            expected_role = (
                DerivationRole.FILLS if run.kind is RunKind.DERIVE else DerivationRole.READS
            )
            if role is not expected_role:
                raise ContractError(
                    f"Run kind {run.kind.value!r} requires derivation role "
                    f"{expected_role.value!r}, not {role.value!r}."
                )
            if run.dataset_id != derivation.dataset_id:
                raise ContractError(
                    f"Run dataset {run.dataset_id!r} does not match derivation dataset "
                    f"{derivation.dataset_id!r}."
                )
            status = session.exec(
                select(DerivationStatus).where(DerivationStatus.derivation_id == derivation_id)
            ).first()
            state = DerivationState.SAVED if status is None else status.state
            if role is DerivationRole.FILLS:
                if run.version_id != derivation.agent_version_id:
                    raise ContractError(
                        f"Derive run agent version {run.version_id!r} does not match derivation "
                        f"agent version {derivation.agent_version_id!r}."
                    )
                if state is not DerivationState.STAGED:
                    raise ContractError("A derive run may only fill a staged derivation.")
            elif state is not DerivationState.SAVED:
                raise ContractError("An evaluator run may only read a saved derivation.")
            link = RunDerivation(run_id=run_id, derivation_id=derivation_id, role=role)
            session.add(link)
            # SQLite drops timezone information on round-trip; refresh so callers receive the
            # same persisted representation returned by ``get_run_derivation``.
            session.flush()
            session.refresh(link)
            return link

    def get_run_derivation(self, run_id: str) -> RunDerivation | None:
        """Return a run's derivation association, if the run has one."""
        with session_scope(self.engine) as session:
            _require(session, Run, run_id)
            return session.exec(
                select(RunDerivation)
                .where(RunDerivation.run_id == run_id)
                .order_by(RunDerivation.created_at)
            ).first()

    def get_run(self, id: str) -> Run:
        """Return the run with ``id`` or raise NotFoundError."""
        with session_scope(self.engine) as session:
            return _require(session, Run, id)

    def list_runs(self, version_id: str | None = None, dataset_id: str | None = None) -> list[Run]:
        """Return runs, optionally filtered by version and/or dataset."""
        with session_scope(self.engine) as session:
            query = select(Run)
            if version_id is not None:
                query = query.where(Run.version_id == version_id)
            if dataset_id is not None:
                query = query.where(Run.dataset_id == dataset_id)
            return list(session.exec(query.order_by(Run.created_at)))

    def update_run_status(self, id: str, status: RunStatus, **fields: object) -> Run:
        """Set a run's status plus any additional fields."""
        with session_scope(self.engine) as session:
            run = _require(session, Run, id)
            run.status = status
            for key, value in fields.items():
                setattr(run, key, value)
            session.add(run)
            return run

    def request_cancel(self, id: str) -> Run:
        """Flag a run for cancellation.

        Raises ContractError for an experiment-engine run: ``Dataset.evaluate`` has no
        cancellation hook, so silently setting the flag would look like it worked while
        doing nothing.
        """
        with session_scope(self.engine) as session:
            run = _require(session, Run, id)
            experiment = session.exec(
                select(ExperimentRun).where(ExperimentRun.run_id == id)
            ).first()
            if experiment is not None:
                raise ContractError(
                    f"Run {id!r} was produced by the experiment engine; experiment runs "
                    "cannot be cancelled."
                )
            run.cancel_requested = True
            session.add(run)
            return run

    def set_experiment(self, run_id: str, **fields: object) -> ExperimentRun:
        """Mark ``run_id`` as produced by the experiment engine, replacing any existing row."""
        with session_scope(self.engine) as session:
            _require(session, Run, run_id)
            existing = session.exec(
                select(ExperimentRun).where(ExperimentRun.run_id == run_id)
            ).first()
            if existing is not None:
                session.delete(existing)
                session.flush()
            experiment = ExperimentRun(run_id=run_id, **fields)
            session.add(experiment)
            return experiment

    def get_experiment(self, run_id: str) -> ExperimentRun | None:
        """Return the experiment-engine marker for ``run_id``, or None for a runner run."""
        with session_scope(self.engine) as session:
            _require(session, Run, run_id)
            return session.exec(select(ExperimentRun).where(ExperimentRun.run_id == run_id)).first()

    def add_result(self, run_id: str, **fields: object) -> RunResult:
        """Record the outcome of scoring one row within a run."""
        with session_scope(self.engine) as session:
            _require(session, Run, run_id)
            result = RunResult(run_id=run_id, **fields)
            session.add(result)
            return result

    def list_results(self, run_id: str) -> list[RunResult]:
        """Return every result of a run ordered by creation time."""
        with session_scope(self.engine) as session:
            return list(
                session.exec(
                    select(RunResult)
                    .where(RunResult.run_id == run_id)
                    .order_by(RunResult.created_at)
                )
            )

    def runs_for_versions(self, version_ids: list[str]) -> list[Run]:
        """Return every run whose version is in ``version_ids``."""
        if not version_ids:
            return []
        with session_scope(self.engine) as session:
            return list(
                session.exec(
                    select(Run).where(Run.version_id.in_(version_ids)).order_by(Run.created_at)
                )
            )

    def runs_for_dataset(self, dataset_id: str) -> list[Run]:
        """Return every run against a dataset."""
        with session_scope(self.engine) as session:
            return list(
                session.exec(
                    select(Run).where(Run.dataset_id == dataset_id).order_by(Run.created_at)
                )
            )

    def failed_result_row_ids(self, run_id: str) -> list[str]:
        """Return the row ids of results that errored within a run."""
        with session_scope(self.engine) as session:
            return list(
                session.exec(
                    select(RunResult.row_id).where(
                        RunResult.run_id == run_id, RunResult.error.is_not(None)
                    )
                )
            )

    # -- Overview -------------------------------------------------------------

    def overview(self) -> Overview:
        """Return store-wide aggregate counts for the Overview page.

        Entity and total-row counts are computed by grouped aggregate SQL. ``labeled_rows``
        cannot be: it comes from each dataset's primary (oldest) label set's annotations, a
        different table from the rows themselves, so it is summed one label set at a time
        rather than by a single grouped join -- mirroring ``list_datasets``/``dataset_stats``.
        Accuracy cannot be aggregated in SQL either, because it lives in the untyped
        ``Run.metrics`` JSON, so completed runs are read back and their accuracy parsed
        defensively in Python. Only runs in the completed terminal state contribute to
        ``best_accuracy`` and are eligible to be the ``latest_run``.
        """
        with session_scope(self.engine) as session:
            evaluator_count = session.exec(select(func.count()).select_from(Evaluator)).one()
            dataset_count = session.exec(select(func.count()).select_from(Dataset)).one()
            run_count = session.exec(select(func.count()).select_from(Run)).one()
            total_rows = session.exec(select(func.count()).select_from(DatasetRow)).one()
            labeled_rows = 0
            _seen_datasets: set[str] = set()
            for label_set in session.exec(select(LabelSet).order_by(LabelSet.created_at)):
                # Only the first (primary) label set per dataset counts, mirroring
                # list_datasets/dataset_stats.
                if label_set.dataset_id in _seen_datasets:
                    continue
                _seen_datasets.add(label_set.dataset_id)
                annotations = session.exec(
                    select(Annotation).where(Annotation.label_set_id == label_set.id)
                ).all()
                labeled_rows += sum(
                    1
                    for annotation in annotations
                    if annotation_ground_truth(label_set, annotation) is not None
                )

            completed = session.exec(
                select(Run, Dataset.name)
                .join(Dataset, Dataset.id == Run.dataset_id)
                .where(Run.status == RunStatus.COMPLETED)
                .order_by(Run.finished_at.desc())
            ).all()

            accuracies = [
                accuracy
                for run, _ in completed
                if (accuracy := _read_accuracy(run.metrics)) is not None
            ]
            best_accuracy = max(accuracies) if accuracies else None

            latest_run: LatestRun | None = None
            if completed:
                # Only COMPLETED runs reach here, and the runner always stamps ``finished_at``
                # on that transition, so ``_as_utc`` cannot return ``None`` for this run — which
                # is why ``LatestRun.finished_at`` is non-optional.
                run, dataset_name = completed[0]
                latest_run = LatestRun(
                    id=run.id,
                    dataset_name=dataset_name,
                    status=run.status,
                    accuracy=_read_accuracy(run.metrics),
                    finished_at=_as_utc(run.finished_at),
                )

            return Overview(
                evaluator_count=evaluator_count,
                dataset_count=dataset_count,
                run_count=run_count,
                total_rows=total_rows,
                labeled_rows=labeled_rows,
                best_accuracy=best_accuracy,
                latest_run=latest_run,
            )
