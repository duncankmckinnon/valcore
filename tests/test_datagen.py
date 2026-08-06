"""Tests for synthetic dataset row generation and post-validation."""

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from valcore.datagen import (
    GeneratedDataset,
    GeneratedRow,
    _apportion,
    _deficit,
    _render_prompt,
    _select,
    _valid_rows,
    _validate_mix,
    generate_rows,
)
from valcore.errors import ConfigError
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


# --- Prescribed label mix: apportionment -----------------------------------

THREE_WAY = LabelSchema(kind=ScoreKind.CATEGORICAL, labels=["pass", "fail", "borderline"])
NUMERIC = LabelSchema(kind=ScoreKind.NUMERIC, minimum=0, maximum=5)


def test_apportion_splits_evenly_when_shares_divide() -> None:
    counts = _apportion({"pass": 0.5, "fail": 0.5}, 10)

    assert counts == {"pass": 5, "fail": 5}


def test_apportion_thirds_sum_to_exactly_count() -> None:
    # 10/3 is not whole, so the leftover row must land somewhere and nowhere twice.
    counts = _apportion({"pass": 1 / 3, "fail": 1 / 3, "borderline": 1 / 3}, 10)

    assert sum(counts.values()) == 10
    assert sorted(counts.values()) == [3, 3, 4]


def test_apportion_gives_leftover_to_largest_remainder() -> None:
    # 0.8*9=7.2 and 0.2*9=1.8, so 'pass' holds the larger remainder and takes the ninth
    # row. 'fail' sorts first alphabetically, so an alphabetical rule would give it 8.
    counts = _apportion({"fail": 0.8, "pass": 0.2}, 9)

    assert counts == {"fail": 7, "pass": 2}


def test_apportion_breaks_remainder_ties_by_label_name() -> None:
    # 0.25*10 and 0.05*10 both leave a remainder of 0.5, so the name decides.
    counts = _apportion({"pass": 0.7, "fail": 0.25, "borderline": 0.05}, 10)

    assert counts == {"pass": 7, "fail": 2, "borderline": 1}


def test_apportion_drops_labels_that_round_to_zero() -> None:
    counts = _apportion({"pass": 0.9, "fail": 0.1}, 2)

    assert "fail" not in counts  # 0.2 rows rounds away rather than asking for zero rows
    assert sum(counts.values()) == 2


def test_apportion_is_deterministic_on_ties() -> None:
    first = _apportion({"b": 0.5, "a": 0.5}, 3)
    second = _apportion({"a": 0.5, "b": 0.5}, 3)

    assert first == second  # tie broken by label name, not dict ordering


# --- Prescribed label mix: validation --------------------------------------


def test_validate_mix_rejects_missing_label_space() -> None:
    with pytest.raises(ConfigError, match="requires a label space"):
        _validate_mix({"pass": 1.0}, None)


def test_validate_mix_rejects_numeric_label_space() -> None:
    with pytest.raises(ConfigError, match="only supported for a categorical"):
        _validate_mix({"pass": 1.0}, NUMERIC)


def test_validate_mix_rejects_unknown_label() -> None:
    with pytest.raises(ConfigError, match="not in the schema"):
        _validate_mix({"pass": 0.5, "unclear": 0.5}, CATEGORICAL)


def test_validate_mix_rejects_negative_share() -> None:
    with pytest.raises(ConfigError, match="negative"):
        _validate_mix({"pass": 1.5, "fail": -0.5}, CATEGORICAL)


def test_validate_mix_rejects_shares_that_do_not_sum_to_one() -> None:
    with pytest.raises(ConfigError, match="must sum to 1.0"):
        _validate_mix({"pass": 0.5, "fail": 0.2}, CATEGORICAL)


def test_validate_mix_rejects_empty_mix() -> None:
    with pytest.raises(ConfigError, match="at least one label"):
        _validate_mix({}, CATEGORICAL)


def test_validate_mix_accepts_hand_written_thirds() -> None:
    # 0.33 + 0.33 + 0.34 is what a human types; it must not be rejected as != 1.0.
    _validate_mix({"pass": 0.33, "fail": 0.33, "borderline": 0.34}, THREE_WAY)


def test_validate_mix_accepts_subset_that_omits_a_label() -> None:
    # Deliberately excluding a label is legal: the omitted label simply gets no rows.
    _validate_mix({"pass": 0.5, "fail": 0.5}, THREE_WAY)


# --- Prescribed label mix: prompt rendering --------------------------------


def test_render_prompt_states_exact_counts_not_percentages() -> None:
    prompt = _render_prompt(
        "desc", ["input", "output"], CATEGORICAL, 10, target={"pass": 6, "fail": 4}
    )

    assert "Produce exactly this many rows for each label:" in prompt
    assert "'pass': 6 row(s)" in prompt
    assert "'fail': 4 row(s)" in prompt
    # The model must never have to do the arithmetic itself.
    assert "%" not in prompt
    assert "0.6" not in prompt


def test_render_prompt_with_target_drops_use_each_label_hint() -> None:
    # The coverage hint would contradict a mix that deliberately gives a label no rows.
    prompt = _render_prompt("desc", ["input", "output"], THREE_WAY, 10, target={"pass": 10})

    assert "at least once" not in prompt
    # The legal label set is still stated, so an out-of-schema label stays out.
    assert "Every suggested_label must be one of" in prompt


def test_render_prompt_keeps_label_guidance_alongside_target() -> None:
    prompt = _render_prompt(
        "desc",
        ["input", "output"],
        CATEGORICAL,
        4,
        label_guidance="treat partial compliance as fail",
        target={"pass": 2, "fail": 2},
    )

    assert "treat partial compliance as fail" in prompt
    assert "'pass': 2 row(s)" in prompt


def test_render_prompt_without_target_is_unchanged() -> None:
    prompt = _render_prompt("desc", ["input", "output"], CATEGORICAL, 3)

    assert "Produce exactly this many rows" not in prompt
    assert "Every suggested_label must be one of" in prompt


# --- Prescribed label mix: deficit-aware top-up ---------------------------


def test_deficit_reports_only_labels_still_short() -> None:
    rows = [
        GeneratedRow(data={}, suggested_label="pass"),
        GeneratedRow(data={}, suggested_label="pass"),
    ]

    assert _deficit({"pass": 3, "fail": 2}, rows) == {"pass": 1, "fail": 2}


def test_deficit_omits_labels_already_over_produced() -> None:
    rows = [GeneratedRow(data={}, suggested_label="pass") for _ in range(5)]

    assert _deficit({"pass": 3, "fail": 1}, rows) == {"fail": 1}


def test_select_fills_quota_before_taking_surplus() -> None:
    rows = [
        GeneratedRow(data={}, suggested_label="pass"),
        GeneratedRow(data={}, suggested_label="pass"),
        GeneratedRow(data={}, suggested_label="pass"),
        GeneratedRow(data={}, suggested_label="fail", reasoning="keep me"),
    ]

    chosen = _select(rows, {"pass": 2, "fail": 2}, 4)

    labels = [row.suggested_label for row in chosen]
    # The surplus third 'pass' must not crowd out the only 'fail'.
    assert labels[:3] == ["pass", "pass", "fail"]
    assert "keep me" in [row.reasoning for row in chosen]


@pytest.mark.anyio
async def test_topup_asks_only_for_the_missing_labels() -> None:
    calls = [0]
    prompts: list[str] = []
    # First call delivers all 3 'pass' rows but no 'fail' rows.
    batches = [[_row("pass")] * 3, [_row("fail")] * 3]
    agent = _agent(_capturing_model(batches, calls, prompts))

    rows = await generate_rows(
        "desc",
        ["input", "output"],
        CATEGORICAL,
        6,
        label_mix={"pass": 0.5, "fail": 0.5},
        agent=agent,
    )

    assert calls[0] == 2
    assert "'pass': 3 row(s)" in prompts[0]
    assert "'fail': 3 row(s)" in prompts[0]
    # The top-up asks for the shortfall only — 3 'fail', and no further 'pass'.
    assert "'fail': 3 row(s)" in prompts[1]
    assert "'pass'" not in prompts[1].split("each label:")[1]
    assert len(rows) == 6


@pytest.mark.anyio
async def test_mix_validation_failure_never_calls_the_model() -> None:
    calls = [0]
    agent = _agent(_batch_model([[_row("pass")]], calls))

    with pytest.raises(ConfigError, match="not in the schema"):
        await generate_rows(
            "desc", ["input", "output"], CATEGORICAL, 2, label_mix={"nope": 1.0}, agent=agent
        )

    assert calls[0] == 0


@pytest.mark.anyio
async def test_mix_omitted_leaves_generation_untouched() -> None:
    calls = [0]
    prompts: list[str] = []
    agent = _agent(_capturing_model([[_row("pass"), _row("fail")]], calls, prompts))

    rows = await generate_rows("desc", ["input", "output"], CATEGORICAL, 2, agent=agent)

    assert len(rows) == 2
    assert "Produce exactly this many rows" not in prompts[0]


def test_apportion_sums_to_count_with_hand_written_thirds() -> None:
    # 0.33+0.33+0.33 is inside the sum tolerance but below 1; the slack must not leave
    # rows unassigned, so shares are normalised before apportioning.
    counts = _apportion({"pass": 0.33, "fail": 0.33, "borderline": 0.33}, 100)

    assert sum(counts.values()) == 100


def test_apportion_sums_to_count_for_a_single_slack_label() -> None:
    # One label carrying a 0.99 share is the worst case: there is nowhere else to put
    # leftover rows, so the normalisation is what keeps the total honest.
    counts = _apportion({"pass": 0.99}, 200)

    assert counts == {"pass": 200}
