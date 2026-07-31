"""SQLModel entities, enums, field specs, and validation helpers."""

import keyword
import string
from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, model_validator
from sqlalchemy import Column
from sqlmodel import JSON, Field, SQLModel

from valcore import settings
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


VALID_CAPABILITIES: frozenset[str] = frozenset(
    {"CodeMode", "SubAgents", "Planning", "FileSystem", "Shell"}
)

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


def check_dataset_compatibility(version: EvaluatorVersion, dataset: Dataset) -> None:
    """Raise ContractError with an actionable message if the dataset and version disagree."""
    missing = [c for c in version.required_columns if c not in dataset.columns]
    if missing:
        raise ContractError(
            f"Dataset {dataset.name!r} is missing required column(s) {missing}; "
            f"it has columns {dataset.columns}."
        )

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
