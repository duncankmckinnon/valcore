"""Shared machinery for agents that draft and refine stored version configs.

Each kind of stored version subclasses ``ConfigGenerator`` with its own config model,
rules, and validation; the run, validate, and retry-once contract lives here once so
every generator behaves the same way.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.exceptions import UnexpectedModelBehavior

from valcore.errors import ConfigError, ContractError
from valcore.local_cli import resolve_model
from valcore.models import CapabilitySpec
from valcore.settings import get_settings


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
