"""Synthetic dataset row generation with suggested labels."""

from pydantic import BaseModel
from pydantic_ai import Agent

from evalcore import settings
from evalcore.errors import ConfigError
from evalcore.models import LabelSchema, ScoreKind

_INSTRUCTIONS = (
    "You generate synthetic evaluation dataset rows for testing LLM judges. "
    "Each row is one example for an evaluator to score, paired with a suggested "
    "label and a short reasoning explaining that label.\n\n"
    "Produce a deliberate mix of clearly passing, clearly failing, and borderline "
    "examples — aim for roughly a third of each. A judge validated only against "
    "good outputs tells you nothing about whether it catches bad ones, so failing "
    "and borderline rows are essential.\n\n"
    "Populate exactly the requested columns in each row's data, using string "
    "values, and label every row within the given label schema."
)


class GeneratedRow(BaseModel):
    """One synthetic row: column data, a suggested label, and its reasoning."""

    data: dict[str, str]
    suggested_label: str | float
    reasoning: str


class GeneratedDataset(BaseModel):
    """A batch of generated rows returned by the datagen agent."""

    rows: list[GeneratedRow]


def build_datagen_agent(model: str | None = None) -> Agent[None, GeneratedDataset]:
    """Build the dataset-generation agent, defaulting to the configured model."""
    resolved = model or settings.get_settings().default_model
    settings.validate_model_string(resolved)
    return Agent(
        resolved,
        output_type=GeneratedDataset,
        name="datagen_agent",
        instructions=_INSTRUCTIONS,
    )


def _render_label_guidance(label_schema: LabelSchema, count: int) -> str:
    """Describe the label space and coverage requirement for the prompt."""
    if label_schema.kind is ScoreKind.CATEGORICAL:
        labels = label_schema.labels or []
        lines = [f"Every suggested_label must be one of: {', '.join(repr(x) for x in labels)}."]
        if count >= len(labels):
            lines.append("Use each of these labels at least once across the rows.")
        return " ".join(lines)

    lo = label_schema.minimum
    hi = label_schema.maximum
    if lo is not None and hi is not None:
        return f"Every suggested_label must be a number between {lo} and {hi} (inclusive)."
    if lo is not None:
        return f"Every suggested_label must be a number greater than or equal to {lo}."
    if hi is not None:
        return f"Every suggested_label must be a number less than or equal to {hi}."
    return "Every suggested_label must be a number."


def _render_prompt(
    description: str, columns: list[str], label_schema: LabelSchema, count: int
) -> str:
    """Render the per-call user prompt for a batch of `count` rows."""
    return (
        f"Generate {count} dataset rows.\n\n"
        f"Description of the data to produce:\n{description}\n\n"
        f"Each row's data must have exactly these columns: {', '.join(columns)}.\n"
        f"{_render_label_guidance(label_schema, count)}\n\n"
        "Remember to include a deliberate mix of passing, failing, and borderline "
        "examples — roughly a third of each."
    )


def _label_valid(label: str | float, label_schema: LabelSchema) -> bool:
    """Return True if `label` is valid under `label_schema`."""
    if label_schema.kind is ScoreKind.CATEGORICAL:
        return isinstance(label, str) and label in (label_schema.labels or [])
    if isinstance(label, bool) or not isinstance(label, (int, float)):
        return False
    below = label_schema.minimum is not None and label < label_schema.minimum
    above = label_schema.maximum is not None and label > label_schema.maximum
    return not (below or above)


def _valid_rows(
    rows: list[GeneratedRow], columns: list[str], label_schema: LabelSchema
) -> list[GeneratedRow]:
    """Drop rows whose columns or suggested label do not fit the schema."""
    wanted = set(columns)
    return [
        row
        for row in rows
        if set(row.data) == wanted and _label_valid(row.suggested_label, label_schema)
    ]


async def generate_rows(
    description: str,
    columns: list[str],
    label_schema: LabelSchema,
    count: int,
    *,
    model: str | None = None,
    agent: Agent | None = None,
) -> list[GeneratedRow]:
    """Generate up to `count` valid rows, making at most one top-up call for shortfall."""
    if count < 1:
        raise ConfigError(f"count must be at least 1, got {count}.")
    if not columns:
        raise ConfigError("At least one column is required to generate rows.")

    agent = agent or build_datagen_agent(model)

    result = await agent.run(_render_prompt(description, columns, label_schema, count))
    valid = _valid_rows(result.output.rows, columns, label_schema)

    if len(valid) < count:
        shortfall = count - len(valid)
        top_up = await agent.run(_render_prompt(description, columns, label_schema, shortfall))
        valid.extend(_valid_rows(top_up.output.rows, columns, label_schema))

    return valid[:count]
