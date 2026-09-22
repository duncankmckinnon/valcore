"""Turn stored evaluator and agent versions into live pydantic-ai agents.

Both builders belong here because this module is the shared boundary between valcore's
persisted version specifications and the executable agents reconstructed from them.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.exceptions import UserError
from pydantic_ai.usage import RunUsage

from valcore import agent_spec
from valcore.agent_spec import AgentSpec
from valcore.capabilities import CAPABILITY_REGISTRY, spec_capability_types
from valcore.errors import ConfigError, ContractError
from valcore.local_cli import resolve_model
from valcore.models import (
    SCALAR_TYPES,
    AgentVersion,
    CapabilitySpec,
    EvaluatorVersion,
    FieldType,
    OutputField,
    parse_output_fields,
    validate_agent_version,
    validate_version,
)
from valcore.settings import is_local_cli_model
from valcore.tools import get_tools

_NUMERIC_FIELD_TYPES: frozenset[FieldType] = frozenset({FieldType.INT, FieldType.FLOAT})


@dataclass(frozen=True)
class AgentExecution:
    """The complete inspectable outcome of running one stored subject-agent version."""

    prompt: str
    deps: dict[str, Any]
    output: dict[str, object]
    response_columns: list[str]
    latency_ms: int
    usage: dict[str, int] | None
    error: str | None


def usage_dict(usage: RunUsage) -> dict[str, int]:
    """Serialize pydantic-ai usage into a plain persistence-safe dictionary."""
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "requests": usage.requests,
    }


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


def build_agent(version: EvaluatorVersion) -> PydanticAgent[None, BaseModel]:
    """Build a live evaluator Agent from a validated version configuration.

    Named ``scoring_agent`` so instrumented traces identify it, rather than falling back to the
    generic ``agent`` an unnamed ``Agent`` produces. The name is set here, not per caller: the
    runner and the experiment engine build the same agent for the same job, and naming it in one
    path only would make the identical agent appear under two names in Logfire and defeat
    comparing a run against an experiment.

    Harness capabilities are never attached for a local CLI model: the CLI already has its own
    native shell/file/sub-agent/planning behavior, so valcore's harness capability wrappers would
    be redundant at best and conflicting at worst. Row tools are not a concern here: validate_version
    already rejects an evaluator version that combines tools with a local model.
    """
    validate_version(version)
    local = is_local_cli_model(version.model)
    specs = [] if local else [CapabilitySpec.model_validate(c) for c in version.capabilities]
    return PydanticAgent(
        resolve_model(version.model),
        output_type=build_output_model(version),
        name="scoring_agent",
        instructions=version.instructions,
        tools=get_tools(version.tools),
        capabilities=build_capabilities(specs),
        defer_model_check=True,
    )


def build_agent_from_version(version: AgentVersion) -> PydanticAgent:
    """Build a live agent from a stored agent version.

    The spec's model is deliberately ignored: valcore route strings preserve gateway key
    handling and local CLI bridge support, neither of which raw pydantic-ai model strings
    express, while settings remains the single place those routes are validated.
    """
    validate_agent_version(version)
    spec = agent_spec.parse_spec(version.spec)
    instructions = agent_spec.runtime_instructions(spec)
    spec_without_instructions = spec.model_copy(update={"instructions": None})
    try:
        return PydanticAgent.from_spec(
            spec_without_instructions,
            model=resolve_model(version.model),
            instructions=instructions,
            custom_capability_types=spec_capability_types(),
            defer_model_check=True,
        )
    except (ValueError, UserError) as exc:
        raise ConfigError(
            f"Agent version {version.version_name!r} could not be built: {exc}"
        ) from exc


def agent_response_data(
    spec: AgentSpec, output: object, *, text_column: str = "response"
) -> dict[str, object]:
    """Map an agent run's output onto the response columns it occupies."""
    if not spec.output_schema or not isinstance(output, dict):
        return {text_column: str(output)}
    return {
        name: output.get(name)
        for name in agent_spec.output_column_names(spec, text_column=text_column)
    }


async def execute_agent_version(
    version: AgentVersion, agent: PydanticAgent, row_data: dict[str, Any]
) -> AgentExecution:
    """Run one subject-agent binding and return success or failure as inspectable data."""
    spec = agent_spec.parse_spec(version.spec)
    prompt = agent_spec.render_agent_prompt(version.prompt_template, row_data)
    deps = agent_spec.build_deps(version.deps_mapping, row_data)
    response_columns = agent_spec.output_column_names(spec)
    start = time.perf_counter()
    try:
        result = await agent.run(prompt, deps=deps)
        return AgentExecution(
            prompt=prompt,
            deps=deps,
            output=agent_response_data(spec, result.output),
            response_columns=response_columns,
            latency_ms=int((time.perf_counter() - start) * 1000),
            usage=usage_dict(result.usage),
            error=None,
        )
    except Exception as exc:  # noqa: BLE001 - model failures are returned for inspection.
        return AgentExecution(
            prompt=prompt,
            deps=deps,
            output={},
            response_columns=response_columns,
            latency_ms=int((time.perf_counter() - start) * 1000),
            usage=None,
            error=str(exc),
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
