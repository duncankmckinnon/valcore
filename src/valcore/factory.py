"""Build a live pydantic_ai.Agent from a stored EvaluatorVersion."""

from collections.abc import Sequence
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model
from pydantic_ai import Agent

from valcore.capabilities import CAPABILITY_REGISTRY
from valcore.errors import ConfigError, ContractError
from valcore.models import (
    SCALAR_TYPES,
    CapabilitySpec,
    EvaluatorVersion,
    FieldType,
    OutputField,
    parse_output_fields,
    validate_version,
)
from valcore.tools import get_tools

_NUMERIC_FIELD_TYPES: frozenset[FieldType] = frozenset({FieldType.INT, FieldType.FLOAT})


def _model_name(version: EvaluatorVersion) -> str:
    """Derive a valid class name for the output model from the version name."""
    name = f"{version.version_name.title().replace(' ', '')}Output"
    return name if name.isidentifier() else "EvaluatorOutput"


def _field_annotation(field: OutputField) -> Any:
    """Return the Python type annotation for an output field."""
    if field.type is FieldType.ENUM:
        return Literal[tuple(field.enum_values or ())]  # type: ignore[valid-type]
    return SCALAR_TYPES[field.type]


def build_output_model(version: EvaluatorVersion) -> type[BaseModel]:
    """Build a Pydantic output model from a version's ordered output field specs."""
    definitions: dict[str, Any] = {}
    for field in parse_output_fields(version):
        annotation = _field_annotation(field)
        field_kwargs: dict[str, Any] = {"description": field.description}
        if field.type in _NUMERIC_FIELD_TYPES:
            if field.minimum is not None:
                field_kwargs["ge"] = field.minimum
            if field.maximum is not None:
                field_kwargs["le"] = field.maximum
        if field.required:
            definitions[field.name] = (annotation, Field(..., **field_kwargs))
        else:
            definitions[field.name] = (annotation | None, Field(None, **field_kwargs))
    return create_model(_model_name(version), **definitions)


def build_capabilities(specs: Sequence[CapabilitySpec]) -> list[Any]:
    """Instantiate harness capability objects from their specs.

    Each capability is imported lazily so a missing optional extra only breaks the
    capability that needs it; a bad import or config raises ConfigError.
    """
    capabilities: list[Any] = []
    for spec in specs:
        entry = CAPABILITY_REGISTRY.get(spec.name)
        if entry is None:
            raise ConfigError(
                f"Unknown capability {spec.name!r}; valid names are {sorted(CAPABILITY_REGISTRY)}."
            )
        try:
            module = __import__(entry.module, fromlist=[entry.class_name])
            cls = getattr(module, entry.class_name)
            capabilities.append(cls(**spec.config))
        except (ImportError, TypeError) as exc:
            raise ConfigError(
                f"Capability {spec.name!r} could not be built from config "
                f"{sorted(spec.config)}: {exc}"
            ) from exc
    return capabilities


def build_agent(version: EvaluatorVersion) -> Agent[None, BaseModel]:
    """Build a live evaluator Agent from a validated version configuration."""
    validate_version(version)
    specs = [CapabilitySpec.model_validate(c) for c in version.capabilities]
    return Agent(
        version.model,
        output_type=build_output_model(version),
        instructions=version.instructions,
        tools=get_tools(version.tools),
        capabilities=build_capabilities(specs),
        defer_model_check=True,
    )


def render_prompt(version: EvaluatorVersion, row_data: dict) -> str:
    """Format the version's prompt template against a row's data.

    Raises ContractError naming the missing column when the template references one
    absent from the row.
    """
    values = {key: str(value) for key, value in row_data.items()}
    try:
        return version.prompt_template.format(**values)
    except KeyError as exc:
        column = exc.args[0]
        raise ContractError(
            f"prompt_template references column {column!r} not present in row data "
            f"(columns: {sorted(row_data)})."
        ) from exc


def extract_score(version: EvaluatorVersion, output: BaseModel) -> str | float:
    """Return the score field's value from an agent output, coercing enums to their value."""
    value = getattr(output, version.score_field)
    if isinstance(value, Enum):
        return value.value
    return value
