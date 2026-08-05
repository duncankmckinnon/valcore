"""Tests for synthetic dataset row generation and post-validation."""

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from valcore.datagen import (
    GeneratedDataset,
    GeneratedRow,
    _render_prompt,
    _valid_rows,
    generate_rows,
)
from valcore.models import LabelSchema, ScoreKind


def _batch_model(batches: list[list[dict]], calls: list[int]) -> FunctionModel:
    """FunctionModel that emits one prepared batch of row dicts per call."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        idx = calls[0]
        calls[0] += 1
        rows = batches[idx] if idx < len(batches) else []
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"rows": rows})])

    return FunctionModel(respond)


def _capturing_model(
    batches: list[list[dict]], calls: list[int], prompts: list[str]
) -> FunctionModel:
    """Like `_batch_model`, but records each call's rendered user prompt into `prompts`."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompts.append(_last_user_prompt(messages))
        idx = calls[0]
        calls[0] += 1
        rows = batches[idx] if idx < len(batches) else []
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"rows": rows})])

    return FunctionModel(respond)


def _last_user_prompt(messages: list[ModelMessage]) -> str:
    """Return the text of the most recent user prompt in a request history."""
    for message in reversed(messages):
        for part in getattr(message, "parts", []):
            if isinstance(part, UserPromptPart):
                return part.content
    return ""


def _agent(model: FunctionModel) -> Agent[None, GeneratedDataset]:
    return Agent(model, output_type=GeneratedDataset, name="datagen_test")


def _row(label: str | float, *, keys: tuple[str, str] = ("input", "output")) -> dict:
    return {"data": {keys[0]: "a", keys[1]: "b"}, "suggested_label": label, "reasoning": "r"}


def _unlabeled_row(*, keys: tuple[str, str] = ("input", "output")) -> dict:
    """A row dict carrying no suggested_label — legal only when the schema is None."""
    return {"data": {keys[0]: "a", keys[1]: "b"}}


CATEGORICAL = LabelSchema(kind=ScoreKind.CATEGORICAL, labels=["pass", "fail"])


@pytest.mark.anyio
async def test_clean_batch_returns_count_rows() -> None:
    calls = [0]
    batch = [_row("pass"), _row("fail"), _row("pass")]
    agent = _agent(_batch_model([batch], calls))

    rows = await generate_rows("desc", ["input", "output"], CATEGORICAL, 3, agent=agent)

    assert len(rows) == 3
    assert [r.suggested_label for r in rows] == ["pass", "fail", "pass"]
    assert calls[0] == 1


@pytest.mark.anyio
async def test_malformed_rows_dropped_and_one_topup_call() -> None:
    calls = [0]
    first = [
        _row("pass"),
        _row("nope"),  # out-of-schema label
        _row("fail", keys=("input", "wrong")),  # wrong data keys
        _row("fail"),
    ]
    topup = [_row("pass"), _row("fail")]
    agent = _agent(_batch_model([first, topup], calls))

    rows = await generate_rows("desc", ["input", "output"], CATEGORICAL, 4, agent=agent)

    assert calls[0] == 2
    assert len(rows) == 4
    assert all(r.suggested_label in {"pass", "fail"} for r in rows)


@pytest.mark.anyio
async def test_persistently_short_returns_fewer_without_looping() -> None:
    calls = [0]
    agent = _agent(_batch_model([[_row("pass")], [_row("fail")]], calls))

    rows = await generate_rows("desc", ["input", "output"], CATEGORICAL, 5, agent=agent)

    assert calls[0] == 2
    assert len(rows) == 2


@pytest.mark.anyio
async def test_numeric_bounds_enforced_at_both_edges() -> None:
    calls = [0]
    schema = LabelSchema(kind=ScoreKind.NUMERIC, minimum=0.0, maximum=1.0)
    batch = [
        _row(0.0),  # lower edge, valid
        _row(1.0),  # upper edge, valid
        _row(-0.1),  # below minimum, dropped
        _row(1.1),  # above maximum, dropped
    ]
    agent = _agent(_batch_model([batch, []], calls))

    rows = await generate_rows("desc", ["input", "output"], schema, 4, agent=agent)

    assert calls[0] == 2  # shortfall triggers exactly one top-up
    assert [r.suggested_label for r in rows] == [0.0, 1.0]


# --- Optional labels -------------------------------------------------------


def test_generated_row_defaults_to_no_label() -> None:
    """The model must accept a row with neither a label nor reasoning."""
    row = GeneratedRow(data={"input": "a", "output": "b"})

    assert row.suggested_label is None
    assert row.reasoning == ""


@pytest.mark.anyio
async def test_unlabeled_rows_kept_when_schema_none() -> None:
    calls = [0]
    batch = [_unlabeled_row(), _unlabeled_row(), _unlabeled_row()]
    agent = _agent(_batch_model([batch], calls))

    rows = await generate_rows("desc", ["input", "output"], None, 3, agent=agent)

    assert calls[0] == 1
    assert len(rows) == 3
    assert all(r.suggested_label is None for r in rows)


@pytest.mark.anyio
async def test_invalid_label_still_dropped_when_schema_present() -> None:
    calls = [0]
    first = [_row("pass"), _row("nope"), _row("fail")]  # "nope" is out of schema
    agent = _agent(_batch_model([first, [_row("pass")]], calls))

    rows = await generate_rows("desc", ["input", "output"], CATEGORICAL, 3, agent=agent)

    assert calls[0] == 2  # the dropped label leaves a shortfall to top up
    assert len(rows) == 3
    assert all(r.suggested_label in {"pass", "fail"} for r in rows)


def test_valid_rows_skips_label_check_when_schema_none() -> None:
    """With no schema, only the exact column-set match gates a row."""
    rows = [
        GeneratedRow(data={"input": "a", "output": "b"}),
        GeneratedRow(data={"input": "a"}),  # wrong column set, still dropped
    ]

    kept = _valid_rows(rows, ["input", "output"], None)

    assert len(kept) == 1
    assert kept[0].suggested_label is None


# --- Exact column-set match holds, with and without notes -------------------


@pytest.mark.anyio
async def test_wrong_columns_dropped_without_notes() -> None:
    calls = [0]
    first = [_row("pass"), _row("fail", keys=("input", "wrong")), _row("pass")]
    agent = _agent(_batch_model([first, [_row("fail")]], calls))

    rows = await generate_rows("desc", ["input", "output"], CATEGORICAL, 3, agent=agent)

    assert calls[0] == 2  # the mismatched row is dropped, forcing a top-up
    assert len(rows) == 3


@pytest.mark.anyio
async def test_wrong_columns_dropped_with_notes() -> None:
    calls = [0]
    notes = {"input": "a short prompt"}
    first = [_row("pass"), _row("fail", keys=("input", "wrong")), _row("pass")]
    agent = _agent(_batch_model([first, [_row("fail")]], calls))

    rows = await generate_rows(
        "desc", ["input", "output"], CATEGORICAL, 3, agent=agent, column_notes=notes
    )

    assert calls[0] == 2
    assert len(rows) == 3


# --- Prompt rendering ------------------------------------------------------


def test_render_prompt_lists_columns_with_notes() -> None:
    notes = {
        "prompt": "jailbreak attempts, 5 languages, varying subtlety",
        "response": "mix of firm refusal and partial compliance",
    }
    prompt = _render_prompt(
        "desc", ["prompt", "response", "locale"], CATEGORICAL, 3, column_notes=notes
    )

    lines = prompt.splitlines()
    assert "Each row's data must have exactly these columns:" in lines
    assert "  - prompt: jailbreak attempts, 5 languages, varying subtlety" in lines
    assert "  - response: mix of firm refusal and partial compliance" in lines


def test_render_prompt_lists_unnoted_column_without_description() -> None:
    notes = {"prompt": "jailbreak attempts, 5 languages, varying subtlety"}
    prompt = _render_prompt(
        "desc", ["prompt", "response", "locale"], CATEGORICAL, 3, column_notes=notes
    )

    lines = prompt.splitlines()
    assert "  - response" in lines  # no note, so no trailing description
    assert "  - locale" in lines


@pytest.mark.parametrize("notes", [None, {}])
def test_render_prompt_single_line_columns_without_notes(notes: dict | None) -> None:
    prompt = _render_prompt("desc", ["input", "output"], CATEGORICAL, 3, column_notes=notes)

    assert "Each row's data must have exactly these columns: input, output." in prompt


def test_render_prompt_appends_label_guidance() -> None:
    prompt = _render_prompt(
        "desc", ["input", "output"], CATEGORICAL, 3, label_guidance="prefer borderline cases"
    )

    assert "prefer borderline cases" in prompt
    # The schema-derived guidance is still present alongside the extra steer.
    assert "Every suggested_label must be one of" in prompt


def test_render_prompt_omits_labels_when_schema_none() -> None:
    prompt = _render_prompt("desc", ["input", "output"], None, 3)

    assert "Every suggested_label must be" not in prompt
    assert "unset" in prompt.lower()  # agent is told to leave suggested_label unset


# --- Top-up carries the same steering --------------------------------------


@pytest.mark.anyio
async def test_topup_call_carries_same_notes_and_guidance() -> None:
    calls = [0]
    prompts: list[str] = []
    notes = {"input": "a jailbreak attempt"}
    guidance = "lean toward subtle, hard-to-catch cases"
    batches = [[_row("pass")], [_row("fail"), _row("pass")]]
    agent = _agent(_capturing_model(batches, calls, prompts))

    rows = await generate_rows(
        "desc",
        ["input", "output"],
        CATEGORICAL,
        3,
        agent=agent,
        column_notes=notes,
        label_guidance=guidance,
    )

    assert calls[0] == 2
    assert len(prompts) == 2  # initial call plus one top-up
    for prompt in prompts:
        assert "a jailbreak attempt" in prompt
        assert guidance in prompt
    assert len(rows) == 3
