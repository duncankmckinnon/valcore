"""Command-construction and output-parsing tests for ClaudeCliAdapter -- no process is executed."""

import json
from pathlib import Path

import pytest

from valcore.local_cli.claude_adapter import ClaudeCliAdapter

# Trimmed from a real `claude -p '...' --output-format json` invocation (2026-09-04);
# unparsed fields (duration_api_ms, session_id, total_cost_usd, ...) are omitted.
_REAL_ENVELOPE = json.dumps(
    {
        "is_error": False,
        "result": '{"verdict": "ok"}',
        "usage": {
            "input_tokens": 2,
            "output_tokens": 980,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 37701,
        },
        "type": "result",
    }
)


def test_claude_adapter_builds_expected_argv(tmp_path: Path) -> None:
    adapter = ClaudeCliAdapter()

    argv = adapter.build_invocation(
        prompt="rate this", instructions="judge it", model_name="sonnet", run_dir=tmp_path
    )

    assert argv[0] == "claude"
    assert "-p" in argv and argv[argv.index("-p") + 1] == "rate this"
    assert "--output-format" in argv and argv[argv.index("--output-format") + 1] == "json"
    assert "--model" in argv and argv[argv.index("--model") + 1] == "sonnet"
    assert "--system-prompt" in argv and argv[argv.index("--system-prompt") + 1] == "judge it"


def test_claude_adapter_parses_a_real_recorded_envelope() -> None:
    adapter = ClaudeCliAdapter()

    text, usage = adapter.parse_output(_REAL_ENVELOPE)

    assert text == '{"verdict": "ok"}'
    assert usage.input_tokens == 2
    assert usage.output_tokens == 980
    assert usage.cache_read_tokens == 0
    assert usage.cache_write_tokens == 37701


def test_claude_adapter_raises_when_envelope_reports_an_error() -> None:
    adapter = ClaudeCliAdapter()
    envelope = json.dumps({"is_error": True, "result": "", "usage": {}})

    with pytest.raises(RuntimeError, match="reported an error"):
        adapter.parse_output(envelope)
