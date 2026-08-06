"""SQLite persistence for valcore entities."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, func, select
from sqlmodel import create_engine as _sqlmodel_create_engine

from valcore import settings
from valcore.errors import (
    ContractError,
    DestructiveChangeError,
    FrozenVersionError,
    NotFoundError,
    ReferencedError,
)
from valcore.models import (
    Dataset,
    DatasetGeneration,
    DatasetRow,
    Evaluator,
    EvaluatorVersion,
    LabelSchema,
    LabelSource,
    Run,
    RunKind,
    RunResult,
    RunStatus,
    validate_version,
)
from valcore.schema_migration import apply_column_changes, invalid_label_ids


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


def _raise_referenced(runs: list[Run], noun: str) -> None:
    """Raise ReferencedError naming the runs that block a delete."""
    run_ids = [run.id for run in runs]
    plural = "run" if len(run_ids) == 1 else "runs"
    raise ReferencedError(
        f"{len(run_ids)} {plural} reference this {noun}; delete them first.",
        detail={"run_count": len(run_ids), "run_ids": run_ids},
    )


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

    # -- Datasets -------------------------------------------------------------

    def create_dataset(
        self,
        name: str,
        description: str,
        columns: list[str],
        label_schema: dict,
    ) -> Dataset:
        """Create and persist a new dataset."""
        with session_scope(self.engine) as session:
            dataset = Dataset(
                name=name,
                description=description,
                columns=columns,
                label_schema=label_schema,
            )
            session.add(dataset)
            return dataset

    def get_dataset(self, id: str) -> Dataset:
        """Return the dataset with ``id`` or raise NotFoundError."""
        with session_scope(self.engine) as session:
            return _require(session, Dataset, id)

    def list_datasets(self) -> list[Dataset]:
        """Return every dataset ordered by creation time."""
        with session_scope(self.engine) as session:
            return list(session.exec(select(Dataset).order_by(Dataset.created_at)))

    def update_dataset(
        self,
        id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        columns: list[str] | None = None,
        column_renames: dict[str, str] | None = None,
        label_schema: dict | None = None,
        force: bool = False,
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

            if label_schema is not None:
                schema = LabelSchema.model_validate(label_schema)
                rows = session.exec(select(DatasetRow).where(DatasetRow.dataset_id == id)).all()
                invalid = invalid_label_ids(rows, schema)
                if invalid and not force:
                    raise DestructiveChangeError(
                        f"{len(invalid)} labels would become invalid under the new schema.",
                        detail={"invalid_label_count": len(invalid)},
                    )
                invalid_ids = set(invalid)
                for row in rows:
                    if row.id in invalid_ids:
                        row.label = None
                        row.label_source = None
                        session.add(row)
                dataset.label_schema = label_schema

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
            # The generation record has no meaning without its dataset; leaving it would
            # orphan a row that nothing can reach.
            generations = session.exec(
                select(DatasetGeneration).where(DatasetGeneration.dataset_id == id)
            )
            for generation in generations:
                session.delete(generation)
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
        """Delete a single dataset row."""
        with session_scope(self.engine) as session:
            row = _require(session, DatasetRow, id)
            session.delete(row)

    def set_label(
        self,
        row_id: str,
        label: dict,
        source: LabelSource,
        note: str | None = None,
    ) -> DatasetRow:
        """Assign a hand- or machine-provided label to a dataset row."""
        with session_scope(self.engine) as session:
            row = _require(session, DatasetRow, row_id)
            row.label = label
            row.label_source = source
            row.note = note
            session.add(row)
            return row

    def labeled_count(self, dataset_id: str) -> tuple[int, int]:
        """Return ``(labeled, total)`` row counts for a dataset."""
        with session_scope(self.engine) as session:
            labels = session.exec(
                select(DatasetRow.label).where(DatasetRow.dataset_id == dataset_id)
            ).all()
            labeled = sum(1 for label in labels if label is not None)
            return labeled, len(labels)

    def label_distribution(self, dataset_id: str) -> dict[str, int]:
        """Return a count of labeled rows keyed by their string label value."""
        with session_scope(self.engine) as session:
            labels = session.exec(
                select(DatasetRow.label).where(DatasetRow.dataset_id == dataset_id)
            ).all()
            distribution: dict[str, int] = {}
            for label in labels:
                if label is None:
                    continue
                key = str(label.get("value"))
                distribution[key] = distribution.get(key, 0) + 1
            return distribution

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
            version = _require(session, EvaluatorVersion, version_id)
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
        """Flag a run for cancellation."""
        with session_scope(self.engine) as session:
            run = _require(session, Run, id)
            run.cancel_requested = True
            session.add(run)
            return run

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
