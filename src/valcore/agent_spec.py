"""Pure validation and derivation over stored pydantic-ai ``AgentSpec`` blobs.

The spec blob belongs to pydantic-ai while valcore owns only its binding to
dataset columns. Keeping that translation pure and I/O-free lets the store and
API validate it identically, and lets it be tested without a database or model
call.
"""

import string
from typing import Any

from pydantic import ValidationError
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.exceptions import UserError

from valcore.errors import ConfigError, ContractError


def parse_spec(blob: dict[str, Any]) -> AgentSpec:
    """Parse a stored blob, exposing malformed specs as a domain error."""
    try:
        return AgentSpec.from_dict(blob)
    except (ValidationError, UserError) as exc:
        raise ConfigError(f"Invalid agent spec: {exc}") from exc


def capability_names(spec: AgentSpec) -> list[str]:
    """Return the spec capability names in their declared order."""
    return [capability.name for capability in spec.capabilities]


def deps_properties(spec: AgentSpec) -> dict[str, Any]:
    """Return the properties declared for structured agent dependencies."""
    return (spec.deps_schema or {}).get("properties", {})


def required_deps_properties(spec: AgentSpec) -> set[str]:
    """Return dependency property names that the spec requires."""
    return set((spec.deps_schema or {}).get("required", []))


def output_column_names(spec: AgentSpec, *, text_column: str = "response") -> list[str]:
    """Return dataset columns occupied by the agent's spec-defined output."""
    if not spec.output_schema:
        return [text_column]
    return list(spec.output_schema.get("properties", {})) or [text_column]


def validate_binding(
    spec: AgentSpec,
    *,
    prompt_template: str,
    required_columns: list[str],
    deps_mapping: dict[str, str],
) -> None:
    """Ensure a valcore binding can satisfy an agent spec from a dataset row."""
    if not required_columns:
        raise ConfigError("Agent version must define at least one required column.")

    template_columns = {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(prompt_template)
        if field_name
    }
    missing_template_columns = sorted(template_columns - set(required_columns))
    if missing_template_columns:
        raise ConfigError(
            "prompt_template references column(s) "
            f"{missing_template_columns} not present in required columns {required_columns}."
        )

    properties = deps_properties(spec)
    for field, column in deps_mapping.items():
        if field not in properties:
            raise ConfigError(
                f"deps_mapping field {field!r} is not a dependency property; "
                f"valid properties are {list(properties)}."
            )
        if column not in required_columns:
            raise ConfigError(
                f"deps_mapping field {field!r} maps to column {column!r}, which is not "
                f"in required columns {required_columns}."
            )

    missing_required_properties = sorted(required_deps_properties(spec) - set(deps_mapping))
    if missing_required_properties:
        raise ConfigError(
            f"deps_mapping does not map required dependency properties {missing_required_properties}."
        )


def build_deps(deps_mapping: dict[str, str], row_data: dict[str, Any]) -> dict[str, Any]:
    """Map row columns to structured dependencies without coercing JSON values."""
    deps: dict[str, Any] = {}
    for field, column in deps_mapping.items():
        try:
            deps[field] = row_data[column]
        except KeyError as exc:
            raise ContractError(
                f"deps_mapping references column {column!r} not present in row data "
                f"(columns: {sorted(row_data)})."
            ) from exc
    return deps


def render_agent_prompt(prompt_template: str, row_data: dict[str, Any]) -> str:
    """Format an agent user prompt from a row, stringifying its values."""
    values = {key: str(value) for key, value in row_data.items()}
    try:
        return prompt_template.format(**values)
    except KeyError as exc:
        column = exc.args[0]
        raise ContractError(
            f"prompt_template references column {column!r} not present in row data "
            f"(columns: {sorted(row_data)})."
        ) from exc
