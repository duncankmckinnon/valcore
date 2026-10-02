"""Draft agent versions from a natural-language description of the agent."""

import re
import string
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent as PydanticAgent

from valcore.capabilities import spec_capability_names
from valcore.errors import ConfigError
from valcore.generator import ConfigGenerator, GeneratedConfigBase, Refinement
from valcore.models import AgentVersion, FieldType, OutputField, validate_agent_version

# A simple column placeholder is letters, digits, and underscores, with no
# attribute access, indexing, conversion, or format specifier.
_SIMPLE_COLUMN = re.compile(r"[A-Za-z0-9_]+")

_JSON_TYPES: dict[FieldType, str] = {
    FieldType.STR: "string",
    FieldType.INT: "integer",
    FieldType.FLOAT: "number",
    FieldType.BOOL: "boolean",
    FieldType.ENUM: "string",
}


def output_schema(fields: list[OutputField]) -> dict[str, Any]:
    """Render output fields as the JSON Schema object an agent spec holds.

    A repeated field name raises ConfigError rather than letting the JSON object silently
    keep only the last field.
    """
    properties: dict[str, Any] = {}
    for field in fields:
        if field.name in properties:
            raise ConfigError(f"Output field names must be unique; {field.name!r} repeats.")
        prop: dict[str, Any] = {"type": _JSON_TYPES[field.type], "description": field.description}
        if field.type is FieldType.ENUM:
            prop["enum"] = field.enum_values
        if field.minimum is not None:
            prop["minimum"] = field.minimum
        if field.maximum is not None:
            prop["maximum"] = field.maximum
        properties[field.name] = prop
    return {
        "type": "object",
        "properties": properties,
        "required": [field.name for field in fields if field.required],
    }


class GeneratedAgentConfig(GeneratedConfigBase):
    """A complete agent version draft produced by the agent generator."""

    output_fields: list[OutputField]
    rationale: str

    def spec(self) -> dict[str, Any]:
        """Return the pydantic-ai spec blob this draft stores, omitting empty sections."""
        spec: dict[str, Any] = {"instructions": self.instructions}
        if self.capabilities:
            spec["capabilities"] = [{c.name: c.config} for c in self.capabilities]
        if self.output_fields:
            spec["output_schema"] = output_schema(self.output_fields)
        return spec

    def to_version(self, model: str) -> AgentVersion:
        """Return the unsaved agent version this draft describes."""
        return AgentVersion(
            agent_id="",
            version_name=self.version_name,
            model=model,
            spec=self.spec(),
            prompt_template=self.prompt_template,
            required_columns=self.required_columns,
            deps_mapping={},
        )


class AgentRefinement(Refinement[GeneratedAgentConfig]):
    """Type the inherited refine flow for agents; no route uses it yet."""


class AgentDraft(BaseModel):
    """An agent version draft as the editor receives it; it carries no model."""

    version_name: str
    spec: dict[str, Any]
    prompt_template: str
    required_columns: list[str]
    deps_mapping: dict[str, str]
    rationale: str

    @classmethod
    def from_config(cls, config: GeneratedAgentConfig) -> "AgentDraft":
        """Build the editor-facing draft from a validated generated config."""
        return cls(
            version_name=config.version_name,
            spec=config.spec(),
            prompt_template=config.prompt_template,
            required_columns=config.required_columns,
            deps_mapping={},
            rationale=config.rationale,
        )


class AgentGenerator(ConfigGenerator[GeneratedAgentConfig]):
    """Draft agent versions from a description of what the agent should do."""

    subject = "agent"
    request_label = "Agent description"
    config_type = GeneratedAgentConfig
    refinement_type = AgentRefinement

    def role_instructions(self) -> str:
        """Describe the generator's job and how to use ``rationale``."""
        return (
            "You design AI agents whose responses will be measured against datasets. "
            "Given a natural-language description of what an agent should do, produce a "
            "complete, valid agent version configuration.\n\n"
            "Use `rationale` to briefly explain why you chose these instructions, inputs, "
            "capabilities, and output shape."
        )

    def allowed_capabilities(self) -> list[str]:
        """Return the harness capabilities an agent spec can reconstruct."""
        return sorted(spec_capability_names())

    def field_rules(self) -> str:
        """Add the agent-specific rules to the shared structural requirements."""
        return (
            super().field_rules()
            + "\n"
            + (
                "- `instructions` is the agent's system prompt: its role, behavior, and "
                "constraints. Do not put `{column}` placeholders in it.\n"
                "- `prompt_template` must contain at least one placeholder, and placeholders "
                "are simple `{column}` names made of letters, digits, and underscores, with no "
                "format specifiers.\n"
                "- `required_columns` must list at least one column.\n"
                "- `output_fields` is empty for an agent that answers in free text. Fill it "
                "only when the description asks for structured output; field names must be "
                "unique identifiers, an enum field needs `enum_values`, and `minimum`/"
                "`maximum` apply only to int and float fields."
            )
        )

    def check_version(self, version: AgentVersion) -> None:
        """Reject a draft that breaks the agent rules or that the store would refuse."""
        _check_agent_prompt_rules(version)
        validate_agent_version(version)


def _placeholders(template: str, *, label: str) -> list[tuple[str, str, str | None]]:
    """Return each ``{field}`` as ``(name, format_spec, conversion)``.

    A template whose braces do not parse raises ConfigError so the generator's
    single validation retry can ask for a corrected draft.
    """
    try:
        # ValueError is raised while iterating, not when parse() is called.
        parsed = list(string.Formatter().parse(template))
    except ValueError as exc:
        raise ConfigError(f"{label} has malformed placeholders: {exc}.") from exc
    found: list[tuple[str, str, str | None]] = []
    for _, field_name, format_spec, conversion in parsed:
        if field_name:
            found.append((field_name, format_spec or "", conversion))
    return found


def _check_agent_prompt_rules(version: AgentVersion) -> None:
    """Enforce the agent instruction, prompt-template, and required-column rules.

    Store validation accepts instructions that contain ``{column}`` placeholders,
    a prompt with no placeholder, format specifiers such as ``{question:>10}``,
    and an empty ``required_columns`` list. Those drafts violate the agent rules,
    so they are rejected here before the store check.
    """
    instructions = version.spec.get("instructions")
    if isinstance(instructions, str):
        # Formatter treats every {...} span as a field, so literal brace text such as
        # JSON is a field whose name is not a column. Only a simple {column} name
        # (letters, digits, and underscores) is a placeholder the agent rules forbid.
        names = [
            name
            for name, _, _ in _placeholders(instructions, label="instructions")
            if _SIMPLE_COLUMN.fullmatch(name)
        ]
        if names:
            raise ConfigError(
                f"instructions must not contain {{column}} placeholders; found {names}."
            )
    prompt_fields = _placeholders(version.prompt_template, label="prompt_template")
    if not prompt_fields:
        raise ConfigError("prompt_template must contain at least one simple {column} placeholder.")
    for name, format_spec, conversion in prompt_fields:
        if conversion or format_spec or _SIMPLE_COLUMN.fullmatch(name) is None:
            raise ConfigError(
                "prompt_template placeholders must be simple {column} names made of "
                "letters, digits, and underscores, with no conversion or format "
                f"specifier; found field {name!r}, format_spec {format_spec!r}, "
                f"conversion {conversion!r}."
            )
    if not version.required_columns:
        raise ConfigError("required_columns must list at least one column.")


async def generate_agent_draft(
    prompt: str, *, model: str | None = None, agent: PydanticAgent | None = None
) -> AgentDraft:
    """Generate an editable agent version draft from a natural-language description."""
    config = await AgentGenerator(model).generate(prompt, agent=agent)
    return AgentDraft.from_config(config)
