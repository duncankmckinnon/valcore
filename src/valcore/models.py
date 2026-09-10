"""SQLModel entities, enums, field specs, and validation helpers."""

import keyword
import string
from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ValidationError, model_validator
from sqlalchemy import Column, UniqueConstraint
from sqlmodel import JSON, Field, SQLModel

from valcore import settings
from valcore.capabilities import VALID_CAPABILITIES
from valcore.errors import ConfigError, ContractError


class ScoreKind(str, Enum):
    """Whether a score is a category label or a number."""

    CATEGORICAL = "categorical"
    NUMERIC = "numeric"


class LabelSource(str, Enum):
    """Provenance of a hand- or machine-assigned dataset label."""

    MANUAL = "manual"
    ACCEPTED = "accepted"
    GENERATED = "generated"


class RunKind(str, Enum):
    """Whether a run validates an evaluator or scores a dataset."""

    VALIDATION = "validation"
    EVAL = "eval"


class RunStatus(str, Enum):
    """Lifecycle state of a run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    CANCELLED = "cancelled"
    FAILED = "failed"


class FieldType(str, Enum):
    """Primitive types allowed for an evaluator output field."""

    STR = "str"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    ENUM = "enum"


# The canonical FieldType -> Python type map lives beside FieldType itself. ``factory`` uses
# it to build runtime annotations and ``export`` derives its source type names from
# ``SCALAR_TYPES[ft].__name__``, so the two never keep separate literal copies. The enum type
# is deliberately absent: an enum renders as a ``Literal`` of its values, not a scalar type.
SCALAR_TYPES: dict[FieldType, type] = {
    FieldType.STR: str,
    FieldType.INT: int,
    FieldType.FLOAT: float,
    FieldType.BOOL: bool,
}

_NUMERIC_FIELD_TYPES: frozenset[FieldType] = frozenset({FieldType.INT, FieldType.FLOAT})


def _bounds_inverted(minimum: float | None, maximum: float | None) -> bool:
    """Return True when both bounds are set and minimum exceeds maximum."""
    return minimum is not None and maximum is not None and minimum > maximum


class OutputField(BaseModel):
    """One field in an evaluator's structured output schema."""

    name: str
    type: FieldType
    description: str
    required: bool = True
    enum_values: list[str] | None = None
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> "OutputField":
        if not self.name.isidentifier() or keyword.iskeyword(self.name):
            raise ValueError(f"Field name {self.name!r} is not a valid Python identifier.")
        if self.type is FieldType.ENUM:
            if not self.enum_values:
                raise ValueError(f"Field {self.name!r} is an enum but has no enum_values.")
        elif self.enum_values is not None:
            raise ValueError(f"Field {self.name!r} sets enum_values but is not an enum.")
        if self.minimum is not None or self.maximum is not None:
            if self.type not in _NUMERIC_FIELD_TYPES:
                raise ValueError(
                    f"Field {self.name!r} sets numeric bounds but is not an int or float."
                )
            if _bounds_inverted(self.minimum, self.maximum):
                raise ValueError(
                    f"Field {self.name!r} has minimum {self.minimum} greater than "
                    f"maximum {self.maximum}."
                )
        return self


class CapabilitySpec(BaseModel):
    """A harness capability attached to an evaluator, by name and config."""

    name: str
    config: dict = {}

    @model_validator(mode="after")
    def _check_name(self) -> "CapabilitySpec":
        if self.name not in VALID_CAPABILITIES:
            raise ValueError(
                f"Unknown capability {self.name!r}; valid names are {sorted(VALID_CAPABILITIES)}."
            )
        return self


class LabelSchema(BaseModel):
    """The label space of a dataset: category labels or a numeric range."""

    kind: ScoreKind
    labels: list[str] | None = None
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> "LabelSchema":
        if self.kind is ScoreKind.CATEGORICAL:
            if not self.labels:
                raise ValueError("Categorical label schema must define non-empty labels.")
            if self.minimum is not None or self.maximum is not None:
                raise ValueError("Categorical label schema must not set numeric bounds.")
        else:
            if self.labels is not None:
                raise ValueError("Numeric label schema must not define labels.")
            if _bounds_inverted(self.minimum, self.maximum):
                raise ValueError(
                    f"Numeric label schema has minimum {self.minimum} greater than "
                    f"maximum {self.maximum}."
                )
        return self


class AnnotationLabel(BaseModel):
    """One label in a label set's contract: its name and the criteria for applying it."""

    name: str
    description: str


class LabelSet(SQLModel, table=True):
    """A named annotation contract on a dataset: a label space plus per-label descriptions.

    A dataset may have any number of label sets. Unlike ``LabelSchema``, a label set carries
    its own identity (``name``/``description``) and, for categorical sets, a description per
    label -- the reference text shown to an annotator alongside each label option. Its label
    space is fixed at creation; only ``name``/``description`` are ever patched afterward, so
    changing the label space means creating a new label set rather than editing this one.
    """

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    dataset_id: str = Field(index=True)
    name: str
    description: str = ""
    kind: ScoreKind
    labels: list[dict] | None = Field(default=None, sa_column=Column(JSON))
    minimum: float | None = None
    maximum: float | None = None


def parse_annotation_labels(label_set: LabelSet) -> list[AnnotationLabel]:
    """Parse and validate a label set's serialized labels into AnnotationLabel specs."""
    return [AnnotationLabel.model_validate(item) for item in (label_set.labels or [])]


def validate_label_set(label_set: LabelSet) -> None:
    """Raise ContractError if a label set's shape is invalid.

    Mirrors ``LabelSchema``'s categorical/numeric mutual exclusion, but as a free function
    (like ``validate_version``) rather than a pydantic model_validator: ``LabelSet`` is a
    persisted table, constructed and re-hydrated by the store, and its shape is only ever
    checked at the point of creation.
    """
    if label_set.kind is ScoreKind.CATEGORICAL:
        if not label_set.labels:
            raise ContractError("Categorical label set must define at least one label.")
        if label_set.minimum is not None or label_set.maximum is not None:
            raise ContractError("Categorical label set must not set numeric bounds.")
        try:
            parsed = parse_annotation_labels(label_set)
        except ValidationError as exc:
            raise ContractError(f"Invalid label set labels: {exc}.") from exc
        names = [label.name for label in parsed]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ContractError(f"Label names must be unique; duplicates: {duplicates}.")
    else:
        if label_set.labels is not None:
            raise ContractError("Numeric label set must not define labels.")
        if _bounds_inverted(label_set.minimum, label_set.maximum):
            raise ContractError(
                f"Numeric label set has minimum {label_set.minimum} greater than "
                f"maximum {label_set.maximum}."
            )


class Annotation(SQLModel, table=True):
    """A row's recorded judgment under one label set: labels or a value, plus a rationale.

    Exactly one row exists per ``(label_set_id, dataset_row_id)`` -- annotating a row again
    edits this record rather than creating a second one. ``suggested_labels``/
    ``suggested_value`` and ``source`` mirror the provenance trail ``DatasetRow`` used to
    carry (suggested vs. accepted vs. manual vs. generated); nothing in this plan writes them
    yet -- that begins when dataset generation is rewired onto this table.
    """

    __table_args__ = (UniqueConstraint("label_set_id", "dataset_row_id", name="uq_annotation_row"),)

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    label_set_id: str = Field(index=True)
    dataset_row_id: str = Field(index=True)
    labels: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    value: float | None = None
    suggested_labels: list[str] | None = Field(default=None, sa_column=Column(JSON))
    suggested_value: float | None = None
    source: LabelSource | None = None
    reasoning: str | None = None
    description: str | None = None


def validate_annotation(
    label_set: LabelSet, *, labels: list[str] | None, value: float | None
) -> None:
    """Raise ContractError if labels/value do not fit label_set's kind and label space."""
    if label_set.kind is ScoreKind.CATEGORICAL:
        if value is not None:
            raise ContractError("Categorical label set annotations must not set a numeric value.")
        allowed = {item["name"] for item in (label_set.labels or [])}
        unknown = sorted(set(labels or []) - allowed)
        if unknown:
            raise ContractError(
                f"Unknown label(s) {unknown} for this label set; valid labels are "
                f"{sorted(allowed)}."
            )
    else:
        if labels:
            raise ContractError("Numeric label set annotations must not set labels.")
        if value is not None:
            below = label_set.minimum is not None and value < label_set.minimum
            above = label_set.maximum is not None and value > label_set.maximum
            if below or above:
                raise ContractError(
                    f"Value {value} is outside this label set's range "
                    f"[{label_set.minimum}, {label_set.maximum}]."
                )


class Evaluator(SQLModel, table=True):
    """A named evaluator with a pointer to its currently active version."""

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    name: str
    description: str = ""
    active_version_id: str | None = None


class EvaluatorVersion(SQLModel, table=True):
    """An immutable-once-frozen configuration snapshot of an evaluator."""

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    evaluator_id: str
    version_name: str
    notes: str = ""
    frozen: bool = False
    model: str
    instructions: str
    prompt_template: str
    required_columns: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    output_fields: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    score_field: str
    score_kind: ScoreKind
    score_labels: list[str] | None = Field(default=None, sa_column=Column(JSON))
    score_minimum: float | None = None
    score_maximum: float | None = None
    capabilities: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    tools: list[str] = Field(default_factory=list, sa_column=Column(JSON))


class Dataset(SQLModel, table=True):
    """A collection of input/output rows with a shared label space."""

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    name: str
    description: str = ""
    columns: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    label_schema: dict = Field(default_factory=dict, sa_column=Column(JSON))


class DatasetGeneration(SQLModel, table=True):
    """How a generated dataset's rows were asked for, kept so a form can be repopulated.

    A separate table rather than columns on ``Dataset``: ``init_db`` is a bare
    ``create_all``, which adds missing tables but never missing columns, so new fields
    here reach an existing database while new ``Dataset`` fields would not. It also keeps
    provenance out of the dataset's own metadata — an uploaded dataset simply has no row.

    ``source_version_id`` is set only by seeded generation, and is provenance rather than
    a live link: the version it names may since have changed or been deleted, so nothing
    reads through it to derive shape.
    """

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    dataset_id: str = Field(index=True)
    count: int = 0
    instructions: str | None = None
    column_notes: dict | None = Field(default=None, sa_column=Column(JSON))
    label_mix: dict | None = Field(default=None, sa_column=Column(JSON))
    label_guidance: str | None = None
    include_labels: bool = True
    source_version_id: str | None = None


class DatasetLogfirePull(SQLModel, table=True):
    """How a dataset's rows were pulled from Logfire, kept so the query can be inspected.

    A separate table rather than columns on ``Dataset``, for the same ``create_all``
    reason as ``DatasetGeneration``. An uploaded, blank, or generated dataset has no row.
    """

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    dataset_id: str = Field(index=True)
    sql: str
    sample_n: int
    seed: int
    min_timestamp: datetime | None = None
    max_timestamp: datetime | None = None
    label_column: str | None = None


class DatasetHostedFetch(SQLModel, table=True):
    """Which hosted Logfire dataset a dataset was fetched from, kept so a refetch can repeat it.

    A separate table rather than columns on ``Dataset``, for the same ``create_all`` reason as
    ``DatasetGeneration``. A SQL-pulled, uploaded, blank, or generated dataset has no row.
    """

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    dataset_id: str = Field(index=True)
    source_name: str


class DatasetRow(SQLModel, table=True):
    """A single row of a dataset with its (optional) hand-assigned label."""

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    dataset_id: str
    idx: int
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    label: dict | None = Field(default=None, sa_column=Column(JSON))
    suggested_label: dict | None = Field(default=None, sa_column=Column(JSON))
    label_reasoning: str | None = None
    label_source: LabelSource | None = None
    note: str | None = None


class Run(SQLModel, table=True):
    """A single execution of an evaluator version over a dataset."""

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    kind: RunKind
    version_id: str
    dataset_id: str
    status: RunStatus
    concurrency: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    metrics: dict | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = None
    cancel_requested: bool = False


class ExperimentRun(SQLModel, table=True):
    """Marks a Run as produced by the pydantic-evals experiment engine rather than the runner.

    A separate table rather than a Run column: ``init_db`` is a bare ``create_all``, which adds
    missing tables but never missing columns, so a new field here reaches an existing database
    while a new ``Run`` field would not. A run with no row is a runner run, which is correct for
    every run that already exists.
    """

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    run_id: str = Field(index=True)
    experiment_name: str
    case_count: int = 0


class RunResult(SQLModel, table=True):
    """The outcome of scoring one dataset row within a run."""

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    run_id: str
    row_id: str
    output: dict | None = Field(default=None, sa_column=Column(JSON))
    score_value: str | float | None = Field(default=None, sa_column=Column(JSON))
    agreement: bool | float | None = Field(default=None, sa_column=Column(JSON))
    latency_ms: int | None = None
    usage: dict | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = None


def _template_columns(template: str) -> set[str]:
    """Return the set of ``{column}`` field names referenced by a prompt template."""
    names: set[str] = set()
    for _, field_name, _, _ in string.Formatter().parse(template):
        if field_name:
            names.add(field_name)
    return names


def parse_output_fields(version: EvaluatorVersion) -> list[OutputField]:
    """Parse and validate a version's serialized output fields into OutputField specs."""
    return [OutputField.model_validate(f) for f in version.output_fields]


def validate_version(version: EvaluatorVersion) -> None:
    """Raise ConfigError if the evaluator version's configuration is invalid."""
    settings.validate_model_string(version.model)

    if version.tools and settings.is_local_cli_model(version.model):
        raise ConfigError(
            f"Evaluator version sets tools {version.tools}, but model {version.model!r} is a "
            "local CLI model, which does not support tool calls. Use a gateway/... model, or "
            "drop tools for this version."
        )

    if not version.output_fields:
        raise ConfigError("Evaluator version must define at least one output field.")

    try:
        fields = parse_output_fields(version)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    names = [f.name for f in fields]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ConfigError(f"Output field names must be unique; duplicates: {duplicates}.")

    by_name = {f.name: f for f in fields}
    if version.score_field not in by_name:
        raise ConfigError(
            f"score_field {version.score_field!r} does not name an output field "
            f"(fields: {sorted(by_name)})."
        )
    score = by_name[version.score_field]

    if version.score_kind is ScoreKind.CATEGORICAL:
        if score.type is not FieldType.ENUM:
            raise ConfigError(
                f"Categorical score field {score.name!r} must be an enum, not {score.type.value}."
            )
        if version.score_labels != score.enum_values:
            raise ConfigError(
                f"Categorical score_labels {version.score_labels} must exactly match the score "
                f"field's enum_values {score.enum_values}."
            )
    else:
        if score.type not in _NUMERIC_FIELD_TYPES:
            raise ConfigError(
                f"Numeric score field {score.name!r} must be an int or float, "
                f"not {score.type.value}."
            )

    if not version.required_columns:
        raise ConfigError("Evaluator version must define at least one required column.")

    missing = sorted(_template_columns(version.prompt_template) - set(version.required_columns))
    if missing:
        raise ConfigError(f"prompt_template references columns not in required_columns: {missing}.")

    for cap in version.capabilities:
        cap_name = cap.get("name")
        if cap_name not in VALID_CAPABILITIES:
            raise ConfigError(
                f"Unknown capability {cap_name!r}; valid names are {sorted(VALID_CAPABILITIES)}."
            )


def check_dataset_compatibility(
    version: EvaluatorVersion, dataset: Dataset, *, kind: RunKind = RunKind.VALIDATION
) -> None:
    """Raise ContractError with an actionable message if the dataset and version disagree.

    Required columns must always be present -- the prompt template reads them, so their absence
    breaks any run. The *label space* checks apply only to ``VALIDATION``, where predictions are
    compared against ground truth and a mismatched vocabulary would make agreement meaningless.
    An ``EVAL`` run never compares, so its score space is free to differ from the dataset's: the
    evaluator can carry finer labels, different wording, or a different kind entirely.

    ``kind`` defaults to ``VALIDATION`` so a caller that does not say what it is running gets the
    stricter contract rather than silently skipping a check it wanted.
    """
    missing = [c for c in version.required_columns if c not in dataset.columns]
    if missing:
        raise ContractError(
            f"Dataset {dataset.name!r} is missing required column(s) {missing}; "
            f"it has columns {dataset.columns}."
        )

    if kind is RunKind.EVAL:
        return

    # An empty label schema is the legal "no ground truth" state: the dataset asserts no
    # label space, so there is nothing to reconcile with the evaluator's score space and
    # the dataset stays runnable (only VALIDATION runs require labels).
    if not dataset.label_schema:
        return

    schema = LabelSchema.model_validate(dataset.label_schema)

    if schema.kind is not version.score_kind:
        raise ContractError(
            f"Dataset label kind {schema.kind.value!r} does not match evaluator score kind "
            f"{version.score_kind.value!r}."
        )

    if version.score_kind is ScoreKind.CATEGORICAL:
        version_labels = set(version.score_labels or [])
        dataset_labels = set(schema.labels or [])
        if version_labels != dataset_labels:
            only_version = sorted(version_labels - dataset_labels)
            only_dataset = sorted(dataset_labels - version_labels)
            raise ContractError(
                "Categorical label sets differ. "
                f"Labels only on the evaluator: {only_version}; "
                f"labels only on the dataset: {only_dataset}."
            )
