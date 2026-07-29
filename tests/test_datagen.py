"""Tests for synthetic dataset row generation and post-validation."""

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from evalcore.datagen import GeneratedDataset, generate_rows
from evalcore.models import LabelSchema, ScoreKind


def _batch_model(batches: list[list[dict]], calls: list[int]) -> FunctionModel:
    """FunctionModel that emits one prepared batch of row dicts per call."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        idx = calls[0]
        calls[0] += 1
        rows = batches[idx] if idx < len(batches) else []
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"rows": rows})])

    return FunctionModel(respond)


def _agent(model: FunctionModel) -> Agent[None, GeneratedDataset]:
    return Agent(model, output_type=GeneratedDataset, name="datagen_test")


def _row(label: str | float, *, keys: tuple[str, str] = ("input", "output")) -> dict:
    return {"data": {keys[0]: "a", keys[1]: "b"}, "suggested_label": label, "reasoning": "r"}


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
