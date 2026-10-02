"""Agents that generate and refine evaluator configurations from natural language."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.exceptions import UnexpectedModelBehavior

from valcore.errors import ConfigError, ContractError
from valcore.local_cli import resolve_model
from valcore.models import (
    VALID_CAPABILITIES,
    CapabilitySpec,
    EvaluatorVersion,
    LabelSchema,
    OutputField,
    ScoreKind,
    validate_version,
)
from valcore.settings import get_settings

try:
    from valcore.tools import tool_names
except ImportError:  # tool registry not yet available in this checkout

    def tool_names() -> list[str]:
        """Fallback returning no registry tools when tools.py is absent."""
        return []


class GeneratedConfigBase(BaseModel):
    """Fields every generated version config shares, emitted first by the model.

    Subclasses add their own fields after these and say how the draft becomes the
    stored version that validation checks.
    """

    version_name: str
    instructions: str
    prompt_template: str
    required_columns: list[str]
    capabilities: list[CapabilitySpec]

    @abstractmethod
    def to_version(self, model: str) -> Any:
        """Return the unsaved stored version this config describes, bound to ``model``."""


ConfigT = TypeVar("ConfigT", bound=GeneratedConfigBase)
OutputT = TypeVar("OutputT", bound=BaseModel)


class Refinement(BaseModel, Generic[ConfigT]):
    """A full config plus a description of what a refinement changed."""

    config: ConfigT
    changed_fields: list[str]
    summary: str


class ConfigGenerator(ABC, Generic[ConfigT]):
    """Drafts and refines one kind of stored version config from natural language.

    The run, validate, and retry-once contract lives here so every kind of generated
    config behaves the same way; subclasses supply the config model, the rules the model
    is given, and how a draft is validated.
    """

    subject: ClassVar[str]
    request_label: ClassVar[str]
    config_type: ClassVar[type[GeneratedConfigBase]]
    refinement_type: ClassVar[type[Refinement[Any]]]

    def __init__(self, model: str | None = None) -> None:
        self.model = model or get_settings().default_model

    @abstractmethod
    def role_instructions(self) -> str:
        """Return the opening paragraph of the generator's instructions."""

    @abstractmethod
    def allowed_capabilities(self) -> list[str]:
        """Return the capability names a generated config may use."""

    @abstractmethod
    def check_version(self, version: Any) -> None:
        """Raise ConfigError if the stored version would be rejected."""

    def field_rules(self) -> str:
        """Return the structural requirements every generated config must satisfy.

        Subclasses append their own bullets with ``super().field_rules() + "\\n" + ...``.
        """
        return (
            "Structural requirements:\n"
            "- `prompt_template` is the per-row user prompt. Every `{column}` placeholder "
            "in it must name one of `required_columns`.\n"
            "- `required_columns` lists every dataset column the prompt template needs.\n"
            f"- `capabilities` may use only these names: {sorted(self.allowed_capabilities())}. "
            "Never invent a capability outside this list."
        )

    def generation_prompt(self, request: str) -> str:
        """Render the natural-language request the generator agent receives."""
        return f"{self.request_label}:\n{request}"

    def validate_config(self, config: ConfigT) -> None:
        """Raise ConfigError if the config would not pass store validation."""
        allowed = self.allowed_capabilities()
        unknown = sorted({capability.name for capability in config.capabilities} - set(allowed))
        if unknown:
            raise ConfigError(f"Unknown capabilities {unknown}; valid names are {sorted(allowed)}.")
        self.check_version(config.to_version(self.model))

    def build_generator_agent(self) -> PydanticAgent[None, ConfigT]:
        """Build the agent that turns a natural-language request into a complete config."""
        return PydanticAgent(
            resolve_model(self.model),
            output_type=self.config_type,
            name=f"{self.subject}_generator",
            # The default budget of one output-validation retry turns a single malformed
            # response into a failed generation, where a retry carrying the validation
            # error usually succeeds.
            retries={"output": 3},
            instructions=f"{self.role_instructions()}\n\n{self.field_rules()}",
        )

    def build_refiner_agent(self) -> PydanticAgent[None, Refinement[ConfigT]]:
        """Build the agent that applies a change request to an existing config."""
        return PydanticAgent(
            resolve_model(self.model),
            output_type=self.refinement_type,
            name=f"{self.subject}_refiner",
            retries={"output": 3},
            instructions=(
                f"You revise an existing {self.subject} configuration given a "
                "natural-language change request. Return the COMPLETE updated configuration "
                "in `config` — every field, not a patch. In `changed_fields`, list exactly "
                "the names of the configuration fields whose values you altered: no more, no "
                "fewer. Use `summary` for a one-line description of the change.\n\n"
                f"{self.field_rules()}"
            ),
        )

    async def generate(self, request: str, *, agent: PydanticAgent | None = None) -> ConfigT:
        """Generate a validated config from a natural-language request."""
        return await self._produce(
            agent or self.build_generator_agent(),
            self.generation_prompt(request),
            lambda output: output,
        )

    async def refine(
        self, current: ConfigT, instruction: str, *, agent: PydanticAgent | None = None
    ) -> Refinement[ConfigT]:
        """Apply a natural-language change request to an existing config."""
        prompt = (
            f"Current configuration:\n{current.model_dump_json(indent=2)}\n\n"
            f"Change request:\n{instruction}"
        )
        return await self._produce(
            agent or self.build_refiner_agent(), prompt, lambda output: output.config
        )

    async def _produce(
        self,
        agent: PydanticAgent[None, OutputT],
        prompt: str,
        extract: Callable[[OutputT], ConfigT],
    ) -> OutputT:
        """Run the agent, then validate its config; on ConfigError retry exactly once.

        An exhausted output-validation budget arrives as ``UnexpectedModelBehavior``, which
        is not a ``ValcoreError`` and would otherwise reach the API's handler table
        unmatched and surface as a 500. It is converted here so callers report something
        actionable.
        """

        async def run(text: str) -> OutputT:
            try:
                return (await agent.run(text)).output
            except UnexpectedModelBehavior as exc:
                raise ContractError(
                    f"The model did not return a usable configuration after retrying: {exc}. "
                    f"Try again, or describe the {self.request_label.lower()} more concretely."
                ) from exc

        output = await run(prompt)
        try:
            self.validate_config(extract(output))
        except ConfigError as exc:
            retry_prompt = (
                f"{prompt}\n\nThe previous configuration was invalid: {exc}\n"
                "Return a corrected, complete configuration."
            )
            output = await run(retry_prompt)
            self.validate_config(extract(output))
        return output


class GeneratedConfig(BaseModel):
    """Complete evaluator version config produced by the generator."""

    name: str
    version_name: str
    instructions: str
    prompt_template: str
    required_columns: list[str]
    output_fields: list[OutputField]
    score_field: str
    score_kind: ScoreKind
    score_labels: list[str] | None = None
    score_minimum: float | None = None
    score_maximum: float | None = None
    capabilities: list[CapabilitySpec]
    tools: list[str]
    rationale: str


class RefinedConfig(BaseModel):
    """A full evaluator config plus a description of what a refinement changed."""

    config: GeneratedConfig
    changed_fields: list[str]
    summary: str


def _field_rules() -> str:
    """Return the structural requirements injected into both agents' instructions.

    Scoped to what `validate_version` actually enforces, so a config that satisfies
    this text is a config that stores and runs. Judge design — how the rubric is
    worded, which score space fits, which capabilities help — is left to the
    criteria rather than dictated here.
    """
    return (
        "Structural requirements:\n"
        "- `instructions` is the system prompt given to the judge.\n"
        "- `output_fields` is an ordered list of at least one field; field names must be "
        "unique.\n"
        "- `score_field` must name one of the `output_fields`.\n"
        "- A categorical score requires `score_kind` 'categorical' and a score field of type "
        "enum whose `enum_values` exactly equal `score_labels`. A numeric score requires "
        "`score_kind` 'numeric', a score field typed int or float, and `score_labels` null.\n"
        "- `required_columns` must list at least one column: every column the judge needs.\n"
        "- `prompt_template` is the per-row user prompt. Every `{column}` placeholder in it "
        "must name one of `required_columns`.\n"
        f"- `capabilities` may use only these names: {sorted(VALID_CAPABILITIES)}.\n"
        f"- `tools` may use only these names: {tool_names()}. Never invent a capability or "
        "tool outside these lists."
    )


def build_generator_agent(model: str | None = None) -> PydanticAgent[None, GeneratedConfig]:
    """Build the agent that turns natural-language criteria into a complete config."""
    instructions = (
        "You design LLM-as-judge evaluators. Given natural-language criteria, produce a "
        "complete, valid evaluator configuration.\n\n"
        f"{_field_rules()}\n\n"
        "Use `rationale` to briefly explain why you chose this schema, scoring, capabilities, "
        "and tools."
    )
    return PydanticAgent(
        resolve_model(model or get_settings().default_model),
        output_type=GeneratedConfig,
        name="evaluator_generator",
        # See build_datagen_agent: the default budget of one output-validation retry turns a
        # single malformed response into a failed generation, where a retry carrying the
        # validation error usually succeeds.
        retries={"output": 3},
        instructions=instructions,
    )


def build_refiner_agent(model: str | None = None) -> PydanticAgent[None, RefinedConfig]:
    """Build the agent that applies a change request to an existing config."""
    instructions = (
        "You revise an existing evaluator configuration given a natural-language change "
        "request. Return the COMPLETE updated configuration in `config` — every field, not a "
        "patch. In `changed_fields`, list exactly the names of the `GeneratedConfig` fields "
        "whose values you altered: no more, no fewer. Use `summary` for a one-line description "
        "of the change.\n\n"
        f"{_field_rules()}"
    )
    return PydanticAgent(
        resolve_model(model or get_settings().default_model),
        output_type=RefinedConfig,
        name="evaluator_refiner",
        # Same reasoning as build_generator_agent.
        retries={"output": 3},
        instructions=instructions,
    )


def _validate(config: GeneratedConfig, model: str) -> None:
    """Raise ConfigError if the generated config would not pass store validation."""
    version = EvaluatorVersion(
        evaluator_id="",
        version_name=config.version_name,
        model=model,
        instructions=config.instructions,
        prompt_template=config.prompt_template,
        required_columns=config.required_columns,
        output_fields=[f.model_dump() for f in config.output_fields],
        score_field=config.score_field,
        score_kind=config.score_kind,
        score_labels=config.score_labels,
        score_minimum=config.score_minimum,
        score_maximum=config.score_maximum,
        capabilities=[c.model_dump() for c in config.capabilities],
        tools=config.tools,
    )
    validate_version(version)


_Output = TypeVar("_Output", bound=BaseModel)


async def _produce(
    agent: PydanticAgent[None, _Output],
    prompt: str,
    model: str,
    extract: Callable[[_Output], GeneratedConfig],
) -> _Output:
    """Run the agent, then validate its config; on ConfigError retry exactly once.

    An exhausted output-validation budget arrives as ``UnexpectedModelBehavior``, which is not a
    ``ValcoreError`` and would otherwise reach the API's handler table unmatched and surface as a
    500. It is converted here so both the API and the CLI report something actionable.
    """

    async def run(text: str) -> _Output:
        try:
            return (await agent.run(text)).output
        except UnexpectedModelBehavior as exc:
            raise ContractError(
                f"The model did not return a usable configuration after retrying: {exc}. "
                "Try again, or describe the criteria more concretely."
            ) from exc

    output = await run(prompt)
    try:
        _validate(extract(output), model)
    except ConfigError as exc:
        retry_prompt = (
            f"{prompt}\n\nThe previous configuration was invalid: {exc}\n"
            "Return a corrected, complete configuration."
        )
        output = await run(retry_prompt)
        _validate(extract(output), model)
    return output


def _columns_section(columns: list[str] | None, column_notes: dict[str, str] | None) -> str:
    """Render the available-columns block, annotating each column's role when given.

    Roles steer both `required_columns` and the `{column}` placeholders, so the
    full column set (bare `columns` plus any keys in `column_notes`) is listed;
    an annotated column that is context-only or ignored can then be excluded by
    the model from both.
    """
    if not columns and not column_notes:
        return ""
    constraints = (
        "\n\nColumn constraints:\n"
        "  - `required_columns` may contain only the dataset columns listed above.\n"
        "  - A column described as irrelevant or ignored must appear in neither "
        "`required_columns` nor `prompt_template`.\n"
        "  - Every `{column}` placeholder in `prompt_template` must name a listed dataset "
        "column; do not invent columns or placeholders."
    )
    if not column_notes:
        # Preserve the pre-existing plain listing when no roles are supplied.
        return f"\n\nAvailable dataset columns: {columns}{constraints}"
    # Union without losing the caller's ordering: listed columns first, then any
    # annotated column not already named in `columns`.
    ordered = list(columns or [])
    for name in column_notes:
        if name not in ordered:
            ordered.append(name)
    lines = [
        f"  - {name}: {column_notes[name]}" if name in column_notes else f"  - {name}"
        for name in ordered
    ]
    return (
        "\n\nAvailable dataset columns and how each factors into the assessment:\n"
        + "\n".join(lines)
        + constraints
    )


def _score_space_section(label_schema: LabelSchema | None) -> str:
    """Render the score-space constraint the config must honour, if one is given.

    Kept as prompt text only: the constraint is stated, never enforced by
    post-processing the agent's output.
    """
    if label_schema is None:
        return ""
    if label_schema.kind is ScoreKind.CATEGORICAL:
        return (
            "\n\nScore-space constraint (the generated config must honour it exactly):\n"
            "  - `score_kind` must be categorical.\n"
            f"  - `score_labels` must equal these labels exactly: {label_schema.labels}."
        )
    return (
        "\n\nScore-space constraint (the generated config must honour it exactly):\n"
        "  - `score_kind` must be numeric.\n"
        f"  - the score bounds must match: minimum {label_schema.minimum}, "
        f"maximum {label_schema.maximum}."
    )


async def generate_config(
    criteria: str,
    *,
    columns: list[str] | None = None,
    column_notes: dict[str, str] | None = None,
    label_schema: LabelSchema | None = None,
    model: str | None = None,
    agent: PydanticAgent | None = None,
) -> GeneratedConfig:
    """Generate a complete evaluator config from natural-language criteria.

    `column_notes` steers which columns become `required_columns` and which
    `{column}` placeholders appear; `label_schema` states the required score
    space as a constraint. Both are prompt steering only — the validate + single
    retry contract is unchanged.
    """
    resolved_model = model or get_settings().default_model
    agent = agent or build_generator_agent(resolved_model)
    prompt = f"Criteria:\n{criteria}"
    prompt += _columns_section(columns, column_notes)
    prompt += _score_space_section(label_schema)
    return await _produce(agent, prompt, resolved_model, lambda out: out)


async def refine_config(
    current: GeneratedConfig,
    instruction: str,
    *,
    model: str | None = None,
    agent: PydanticAgent | None = None,
) -> RefinedConfig:
    """Apply a natural-language change request to an existing evaluator config."""
    resolved_model = model or get_settings().default_model
    agent = agent or build_refiner_agent(resolved_model)
    prompt = (
        f"Current configuration:\n{current.model_dump_json(indent=2)}\n\n"
        f"Change request:\n{instruction}"
    )
    return await _produce(agent, prompt, resolved_model, lambda out: out.config)
