"""Synthetic dataset row generation with suggested labels."""

from collections import Counter

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior

from valcore import settings
from valcore.errors import ConfigError, ContractError
from valcore.models import LabelSchema, ScoreKind

# Proportions are authored by hand, so 1/3 + 1/3 + 1/3 must be accepted as summing to 1.
_MIX_SUM_TOLERANCE = 0.01

_INSTRUCTIONS = (
    "You generate synthetic evaluation dataset rows. Each row is one example for an "
    "evaluator to score, paired with an optional suggested label and a short reasoning "
    "explaining that label.\n\n"
    "Structural requirements:\n"
    "- Each row's data must contain exactly the requested columns, with string values.\n"
    "- Every suggested label must fall within the given label schema; when no label "
    "space is given, leave the label unset.\n\n"
    "The prompt describes what the rows should contain. Follow it — it, not this "
    "message, decides the content, difficulty, and distribution of the examples."
)


class GeneratedRow(BaseModel):
    """One synthetic row: column data, an optional suggested label, and its reasoning."""

    data: dict[str, str]
    # A label is optional: datasets need no ground truth, so a schema-less
    # generation leaves this unset rather than inventing a label space.
    suggested_label: str | float | None = None
    reasoning: str = ""


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
        # pydantic-ai budgets one output-validation retry by default, so a single malformed
        # response -- e.g. calling the output tool with `{}`, which fails as "rows: Field
        # required" -- discards the whole batch. A retry feeds the validation error back to the
        # model, which usually corrects it, so the budget is raised rather than left at the
        # default that turns one bad response into a failed generation.
        retries={"output": 3},
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


def _validate_mix(label_mix: dict[str, float], label_schema: LabelSchema | None) -> None:
    """Raise ConfigError if `label_mix` cannot describe a distribution over `label_schema`.

    A mix names labels, so it is meaningful only for a categorical space. Refusing the
    numeric and schema-less cases keeps a caller's distribution from being silently
    dropped on the floor.
    """
    if label_schema is None:
        raise ConfigError("label_mix requires a label space, but none was given.")
    if label_schema.kind is not ScoreKind.CATEGORICAL:
        raise ConfigError(
            f"label_mix is only supported for a categorical label space, not "
            f"{label_schema.kind.value}."
        )
    if not label_mix:
        raise ConfigError("label_mix must name at least one label.")

    labels = label_schema.labels or []
    unknown = sorted(label for label in label_mix if label not in labels)
    if unknown:
        raise ConfigError(f"label_mix references label(s) {unknown} not in the schema {labels}.")

    negative = sorted(label for label, share in label_mix.items() if share < 0)
    if negative:
        raise ConfigError(f"label_mix proportion(s) for {negative} are negative.")

    total = sum(label_mix.values())
    if abs(total - 1.0) > _MIX_SUM_TOLERANCE:
        raise ConfigError(f"label_mix proportions must sum to 1.0, got {total}.")


def _apportion(label_mix: dict[str, float], count: int) -> dict[str, int]:
    """Turn proportions into whole row counts that sum to exactly `count`.

    Truncating each share leaves rows unassigned, so the remainder goes to the labels
    with the largest fractional parts (largest-remainder apportionment), ties broken by
    label name to keep the result deterministic. Labels apportioned zero rows are
    dropped: the prompt should ask for rows, not for their absence.

    Shares are normalised by their own total first. `_validate_mix` only requires them to
    sum to 1 within a tolerance, and spending that slack here would leave more rows
    unassigned than there are labels to receive them — an under-count of `count`.
    """
    total = sum(label_mix.values())
    exact = {label: share / total * count for label, share in label_mix.items()}
    counts = {label: int(value) for label, value in exact.items()}
    leftover = count - sum(counts.values())
    by_remainder = sorted(exact, key=lambda label: (-(exact[label] - counts[label]), label))
    for label in by_remainder[:leftover]:
        counts[label] += 1
    return {label: value for label, value in counts.items() if value > 0}


def _deficit(target: dict[str, int], rows: list[GeneratedRow]) -> dict[str, int]:
    """Return how many rows each label is still short of `target`."""
    produced = Counter(row.suggested_label for row in rows)
    shortfalls = {label: want - produced[label] for label, want in target.items()}
    return {label: short for label, short in shortfalls.items() if short > 0}


def _select(rows: list[GeneratedRow], target: dict[str, int], count: int) -> list[GeneratedRow]:
    """Take `count` rows, filling each label's quota before spending slots on surplus.

    A plain head-of-list truncation would let an over-produced label crowd out rows that
    satisfy the requested mix, so quota-filling rows are kept in order first and surplus
    rows only backfill whatever room is left.
    """
    quota = dict(target)
    within: list[GeneratedRow] = []
    surplus: list[GeneratedRow] = []
    for row in rows:
        label = row.suggested_label
        if quota.get(label, 0) > 0:
            quota[label] -= 1
            within.append(row)
        else:
            surplus.append(row)
    return (within + surplus)[:count]


def _render_columns(columns: list[str], column_notes: dict[str, str] | None) -> str:
    """Render the exact-columns instruction, expanding to per-column notes when given.

    Without notes we keep the original single-line form so existing behavior and its
    tests are untouched; with notes each column gets its own line and optional steer.
    """
    if not column_notes:
        return f"Each row's data must have exactly these columns: {', '.join(columns)}."

    lines = ["Each row's data must have exactly these columns:"]
    for column in columns:
        note = column_notes.get(column)
        lines.append(f"  - {column}: {note}" if note else f"  - {column}")
    return "\n".join(lines)


def _render_target(target: dict[str, int]) -> str:
    """Render the per-label row counts the batch must hit, as exact counts never percentages."""
    lines = [f"  - {label!r}: {want} row(s)" for label, want in target.items()]
    return "Produce exactly this many rows for each label:\n" + "\n".join(lines)


def _render_labels(
    label_schema: LabelSchema | None,
    label_guidance: str | None,
    count: int,
    target: dict[str, int] | None = None,
) -> str:
    """Render label instructions, or tell the agent to leave labels unset when schema-less."""
    if label_schema is None:
        return "This dataset has no label space; leave suggested_label unset for every row."

    if target is None:
        guidance = _render_label_guidance(label_schema, count)
        return f"{guidance} {label_guidance}" if label_guidance else guidance

    # An explicit per-label count supersedes the "use each label once" coverage hint,
    # which would otherwise contradict a mix that deliberately omits a label.
    labels = label_schema.labels or []
    parts = [
        f"Every suggested_label must be one of: {', '.join(repr(x) for x in labels)}.",
        _render_target(target),
    ]
    if label_guidance:
        parts.append(label_guidance)
    return "\n".join(parts)


def _render_prompt(
    description: str,
    columns: list[str],
    label_schema: LabelSchema | None,
    count: int,
    *,
    column_notes: dict[str, str] | None = None,
    label_guidance: str | None = None,
    target: dict[str, int] | None = None,
) -> str:
    """Render the per-call user prompt for a batch of `count` rows."""
    return (
        f"Generate {count} dataset rows.\n\n"
        f"Description of the data to produce:\n{description}\n\n"
        f"{_render_columns(columns, column_notes)}\n"
        f"{_render_labels(label_schema, label_guidance, count, target)}"
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
    rows: list[GeneratedRow], columns: list[str], label_schema: LabelSchema | None
) -> list[GeneratedRow]:
    """Drop rows whose columns or suggested label do not fit the schema.

    The exact column-set match always gates a row — it is what guarantees the caller's
    fixed column set is respected. The label check is skipped entirely when there is no
    schema, since a schema-less dataset carries no ground truth to validate.
    """
    wanted = set(columns)
    return [
        row
        for row in rows
        if set(row.data) == wanted
        and (label_schema is None or _label_valid(row.suggested_label, label_schema))
    ]


async def generate_rows(
    description: str,
    columns: list[str],
    label_schema: LabelSchema | None,
    count: int,
    *,
    column_notes: dict[str, str] | None = None,
    label_guidance: str | None = None,
    label_mix: dict[str, float] | None = None,
    model: str | None = None,
    agent: Agent | None = None,
) -> list[GeneratedRow]:
    """Generate up to `count` valid rows, making at most one top-up call for shortfall.

    `label_mix` maps each label to its share of `count`; the shares are apportioned to
    whole rows before rendering, so the prompt asks for exact counts. It steers the
    model rather than binding it — with one top-up call there is no guarantee every
    quota is met, but the top-up asks only for the labels still missing and the final
    selection prefers rows that fill the quotas.
    """
    if count < 1:
        raise ConfigError(f"count must be at least 1, got {count}.")
    if not columns:
        raise ConfigError("At least one column is required to generate rows.")

    target: dict[str, int] | None = None
    if label_mix is not None:
        _validate_mix(label_mix, label_schema)
        target = _apportion(label_mix, count)

    agent = agent or build_datagen_agent(model)

    def render(batch: int, batch_target: dict[str, int] | None) -> str:
        return _render_prompt(
            description,
            columns,
            label_schema,
            batch,
            column_notes=column_notes,
            label_guidance=label_guidance,
            target=batch_target,
        )

    async def run(prompt: str) -> object:
        """Run the agent, converting an exhausted-retries failure into a domain error.

        ``UnexpectedModelBehavior`` is not a ``ValcoreError``, so it reaches the API's handler
        table unmatched and surfaces as a 500 with a traceback rather than the actionable client
        error a recoverable model failure deserves.
        """
        try:
            return await agent.run(prompt)
        except UnexpectedModelBehavior as exc:
            raise ContractError(
                f"The model did not return a usable dataset after retrying: {exc}. "
                "Try again, lower the row count, or make the column notes more specific."
            ) from exc

    result = await run(render(count, target))
    valid = _valid_rows(result.output.rows, columns, label_schema)

    if len(valid) < count:
        shortfall = count - len(valid)
        deficit = _deficit(target, valid) if target else None
        batch = sum(deficit.values()) if deficit else shortfall
        top_up = await run(render(batch, deficit))
        valid.extend(_valid_rows(top_up.output.rows, columns, label_schema))

    return _select(valid, target, count) if target else valid[:count]
