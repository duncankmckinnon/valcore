"""SQLite persistence for valcore entities."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, func, select
from sqlmodel import create_engine as _sqlmodel_create_engine

from valcore import settings
from valcore.errors import (
    ContractError,
    FrozenVersionError,
    NotFoundError,
    ReferencedError,
)
from valcore.models import (
    Annotation,
    Dataset,
    DatasetGeneration,
    DatasetHostedFetch,
    DatasetLogfirePull,
    DatasetRow,
    Evaluator,
    EvaluatorVersion,
    ExperimentRun,
    LabelSet,
    LabelSource,
    Run,
    RunKind,
    RunResult,
    RunStatus,
    ScoreKind,
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


def _raise_referenced(runs: list[Run], noun: str) -> None:
    """Raise ReferencedError naming the runs that block a delete."""
    run_ids = [run.id for run in runs]
    plural = "run" if len(run_ids) == 1 else "runs"
    raise ReferencedError(
        f"{len(run_ids)} {plural} reference this {noun}; delete them first.",
        detail={"run_count": len(run_ids), "run_ids": run_ids},
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
                labeled_count = (
                    session.exec(
                        select(func.count())
                        .select_from(Annotation)
                        .where(Annotation.label_set_id == primary.id)
                    ).one()
                    if primary is not None
                    else 0
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
                labeled_rows += session.exec(
                    select(func.count())
                    .select_from(Annotation)
                    .where(Annotation.label_set_id == label_set.id)
                ).one()

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
