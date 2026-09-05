"""Command-construction and output-parsing tests for CodexCliAdapter -- no process is executed."""

import json
from pathlib import Path

import pytest

from valcore.local_cli.codex_adapter import CodexCliAdapter

# Trimmed from a real `codex exec --json '...'` invocation (2026-09-04).
_REAL_JSONL = "\n".join(
    [
        json.dumps({"type": "thread.started", "thread_id": "01a06f20-719e-7311-924a-798f651541df"}),
        json.dumps({"type": "turn.started"}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": '{"verdict": "ok"}'},
            }
        ),
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 17659,
                    "cached_input_tokens": 11136,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 11,
                },
            }
        ),
    ]
)


def test_codex_adapter_builds_expected_argv(tmp_path: Path) -> None:
    adapter = CodexCliAdapter()

    argv = adapter.build_invocation(
        prompt="rate this", instructions="judge it", model_name="gpt-5-codex", run_dir=tmp_path
    )

    assert argv[0] == "codex"
    assert argv[1] == "exec"
    assert "--json" in argv
    assert "--model" in argv and argv[argv.index("--model") + 1] == "gpt-5-codex"
    assert argv[-1] == "judge it\n\nrate this"


def test_codex_adapter_omits_the_separator_when_there_are_no_instructions(tmp_path: Path) -> None:
    adapter = CodexCliAdapter()

    argv = adapter.build_invocation(
        prompt="rate this", instructions="", model_name="gpt-5-codex", run_dir=tmp_path
    )

    assert argv[-1] == "rate this"


def test_codex_adapter_parses_a_real_recorded_jsonl_stream() -> None:
    adapter = CodexCliAdapter()

    text, usage = adapter.parse_output(_REAL_JSONL)

    assert text == '{"verdict": "ok"}'
    assert usage.input_tokens == 17659
    assert usage.output_tokens == 11
    assert usage.cache_read_tokens == 11136


def test_codex_adapter_raises_when_no_agent_message_is_present() -> None:
    adapter = CodexCliAdapter()
    stream = json.dumps({"type": "turn.completed", "usage": {}})

    with pytest.raises(RuntimeError, match="no agent_message"):
        adapter.parse_output(stream)
