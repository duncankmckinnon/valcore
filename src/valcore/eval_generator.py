"""Generate and refine evaluator configurations from natural language."""

from pydantic_ai import Agent as PydanticAgent

from valcore.errors import ConfigError
from valcore.generator import ConfigGenerator, GeneratedConfigBase, Refinement
from valcore.models import (
    VALID_CAPABILITIES,
    EvaluatorVersion,
    LabelSchema,
    OutputField,
    ScoreKind,
    validate_version,
)
from valcore.tools import tool_names


class GeneratedConfig(GeneratedConfigBase):
    """Complete evaluator version config produced by the generator."""

    name: str
    output_fields: list[OutputField]
    score_field: str
    score_kind: ScoreKind
    score_labels: list[str] | None = None
    score_minimum: float | None = None
    score_maximum: float | None = None
    tools: list[str]
    rationale: str

    def to_version(self, model: str) -> EvaluatorVersion:
        """Build the unsaved evaluator version used for structural validation."""
        return EvaluatorVersion(
            evaluator_id="",
            version_name=self.version_name,
            model=model,
            instructions=self.instructions,
            prompt_template=self.prompt_template,
            required_columns=self.required_columns,
            output_fields=[f.model_dump() for f in self.output_fields],
            score_field=self.score_field,
            score_kind=self.score_kind,
            score_labels=self.score_labels,
            score_minimum=self.score_minimum,
            score_maximum=self.score_maximum,
            capabilities=[c.model_dump() for c in self.capabilities],
            tools=self.tools,
        )


class RefinedConfig(Refinement[GeneratedConfig]):
    """A full evaluator config plus a description of what a refinement changed."""


class EvaluatorGenerator(ConfigGenerator[GeneratedConfig]):
    """Drafts LLM-as-judge evaluator configs, optionally shaped by a dataset."""

    subject = "evaluator"
    request_label = "Criteria"
    config_type = GeneratedConfig
    refinement_type = RefinedConfig

    def __init__(
        self,
        model: str | None = None,
        *,
        columns: list[str] | None = None,
        column_notes: dict[str, str] | None = None,
        label_schema: LabelSchema | None = None,
    ) -> None:
        """Keep optional dataset shape and score constraints for the draft prompt."""
        super().__init__(model)
        self.columns = columns
        self.column_notes = column_notes
        self.label_schema = label_schema

    def role_instructions(self) -> str:
        """Describe the evaluator designer's task to the generator agent."""
        return (
            "You design LLM-as-judge evaluators. Given natural-language criteria, "
            "produce a complete, valid evaluator configuration.\n\n"
            "Use `rationale` to briefly explain why you chose this schema, scoring, "
            "capabilities, and tools."
        )

    def allowed_capabilities(self) -> list[str]:
        """Return the capability names valid for evaluator versions."""
        return sorted(VALID_CAPABILITIES)

    def field_rules(self) -> str:
        """Add evaluator score, output, and tool constraints to the shared rules."""
        return (
            super().field_rules()
            + "\n"
            + (
                "- `instructions` is the system prompt given to the judge.\n"
                "- `output_fields` is an ordered list of at least one field; field names "
                "must be unique.\n"
                "- `score_field` must name one of the `output_fields`.\n"
                "- A categorical score requires `score_kind` 'categorical' and a score "
                "field of type enum whose `enum_values` exactly equal `score_labels`. A "
                "numeric score requires `score_kind` 'numeric', a score field typed int or "
                "float, and `score_labels` null.\n"
                "- `required_columns` must list at least one column: every column the "
                "judge needs.\n"
                f"- `tools` may use only these names: {tool_names()}. Never invent a tool "
                "outside this list."
            )
        )

    def check_version(self, version: EvaluatorVersion) -> None:
        """Reject unknown tools, then apply the stored evaluator version rules."""
        valid = sorted(tool_names())
        unknown = sorted(set(version.tools) - set(valid))
        if unknown:
            raise ConfigError(f"Unknown tools {unknown}; valid names are {valid}.")
        validate_version(version)

    def generation_prompt(self, request: str) -> str:
        """Append any supplied dataset columns and score-space guidance."""
        return (
            super().generation_prompt(request)
            + _columns_section(self.columns, self.column_notes)
            + _score_space_section(self.label_schema)
        )


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
    generator = EvaluatorGenerator(
        model, columns=columns, column_notes=column_notes, label_schema=label_schema
    )
    return await generator.generate(criteria, agent=agent)


async def refine_config(
    current: GeneratedConfig,
    instruction: str,
    *,
    model: str | None = None,
    agent: PydanticAgent | None = None,
) -> RefinedConfig:
    """Apply a natural-language change request to an existing evaluator config."""
    return await EvaluatorGenerator(model).refine(current, instruction, agent=agent)
