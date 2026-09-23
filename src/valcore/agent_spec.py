"""Pure validation and derivation over stored pydantic-ai ``AgentSpec`` blobs.

The spec blob belongs to pydantic-ai while valcore owns only its binding to
dataset columns. Keeping that translation pure and I/O-free lets the store and
API validate it identically, and lets it be tested without a database or model
call.
"""

import json
import re
import string
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import ValidationError
from pydantic_ai import RunContext
from pydantic_ai.agent.spec import AgentSpec as PydanticAgentSpec
from pydantic_ai.exceptions import UserError

from valcore.errors import ConfigError, ContractError


class AgentSpec(PydanticAgentSpec):
    """Agent spec validated without Pydantic AI's optional Handlebars dependency.

    Valcore stores instruction templates as portable strings and renders their dependency
    placeholders locally when building the agent. All other fields retain the upstream
    ``AgentSpec`` validation and serialization contract.
    """

    description: str | None = None
    instructions: str | list[str] | None = None


_HANDLEBARS_VARIABLE = re.compile(r"{{\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*}}")


def parse_spec(blob: dict[str, Any]) -> AgentSpec:
    """Parse a stored blob, exposing malformed specs as a domain error."""
    try:
        return AgentSpec.from_dict(blob)
    except (ValidationError, UserError) as exc:
        raise ConfigError(f"Invalid agent spec: {exc}") from exc


def _dependency_value(deps: object, path: str) -> object:
    """Resolve one dotted instruction-template variable from run dependencies."""
    value = deps
    for part in path.split("."):
        try:
            value = value[part] if isinstance(value, Mapping) else getattr(value, part)
        except (KeyError, AttributeError) as exc:
            raise ConfigError(
                f"Instruction template references dependency {path!r}, which is unavailable."
            ) from exc
    return value


def _render_instruction(template: str, deps: object) -> str:
    """Render simple Handlebars-style dependency variables without an optional package."""
    rendered = _HANDLEBARS_VARIABLE.sub(
        lambda match: str(_dependency_value(deps, match.group(1))), template
    )
    if "{{" in rendered or "}}" in rendered:
        raise ConfigError(
            "Instruction templates support dependency variables such as '{{customer.tier}}'."
        )
    return rendered


def _instruction_renderer(template: str) -> Callable[[RunContext[Any]], str]:
    """Bind a template into the callable instruction form accepted by Pydantic AI."""

    def render(ctx: RunContext[Any]) -> str:
        return _render_instruction(template, ctx.deps)

    return render


def runtime_instructions(spec: AgentSpec) -> list[str | Callable[[RunContext[Any]], str]]:
    """Return static or locally rendered instructions for constructing a live agent."""
    if spec.instructions is None:
        return []
    instructions = spec.instructions if isinstance(spec.instructions, list) else [spec.instructions]
    return [
        _instruction_renderer(instruction) if "{{" in instruction else instruction
        for instruction in instructions
    ]


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
    if not prompt_template:
        if set(row_data) == {"input"}:
            return str(row_data["input"])
        return json.dumps(row_data, ensure_ascii=False, indent=2) if row_data else ""
    values = {key: str(value) for key, value in row_data.items()}
    try:
        return prompt_template.format(**values)
    except KeyError as exc:
        column = exc.args[0]
        raise ContractError(
            f"prompt_template references column {column!r} not present in row data "
            f"(columns: {sorted(row_data)})."
        ) from exc
