"""Command-construction and output-parsing tests for CursorCliAdapter -- no process is executed."""

import json
from pathlib import Path

from valcore.local_cli.cursor_adapter import CursorCliAdapter

# Trimmed from a real `cursor-agent -p '...' --output-format json --workspace <dir> --trust`
# invocation (2026-09-04); unparsed fields (duration_ms, session_id, request_id, ...) omitted.
_REAL_ENVELOPE = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": '{"verdict": "ok"}',
        "usage": {
            "inputTokens": 11581,
            "outputTokens": 156,
            "cacheReadTokens": 5376,
            "cacheWriteTokens": 0,
        },
    }
)


def test_cursor_adapter_builds_expected_argv(tmp_path: Path) -> None:
    adapter = CursorCliAdapter()

    argv = adapter.build_invocation(
        prompt="rate this", instructions="judge it", model_name="composer", run_dir=tmp_path
    )

    assert argv[0] == "cursor-agent"
    assert "-p" in argv
    assert argv[-1] == "judge it\n\nrate this"
    assert "--model" in argv and argv[argv.index("--model") + 1] == "composer"
    assert "--workspace" in argv and argv[argv.index("--workspace") + 1] == str(tmp_path)
    assert "--trust" in argv


def test_cursor_adapter_passes_the_prompt_after_a_bare_double_dash(tmp_path: Path) -> None:
    """A prompt starting with '-' must not be parsed as a CLI option."""
    adapter = CursorCliAdapter()

    argv = adapter.build_invocation(
        prompt="- dash bullet prompt", instructions="", model_name="composer", run_dir=tmp_path
    )

    assert argv[-1] == "- dash bullet prompt"
    assert argv[-2] == "--"
    assert argv.count("--") == 1


def test_cursor_adapter_runs_in_ask_mode(tmp_path: Path) -> None:
    """Row content is untrusted; a prompt->JSON judge needs no file or shell tools."""
    adapter = CursorCliAdapter()

    argv = adapter.build_invocation(
        prompt="rate this", instructions="judge it", model_name="composer", run_dir=tmp_path
    )

    assert "--mode" in argv
    assert argv[argv.index("--mode") + 1] == "ask"


def test_cursor_adapter_parses_a_real_recorded_envelope() -> None:
    adapter = CursorCliAdapter()

    text, usage = adapter.parse_output(_REAL_ENVELOPE)

    assert text == '{"verdict": "ok"}'
    assert usage.input_tokens == 11581
    assert usage.output_tokens == 156
    assert usage.cache_read_tokens == 5376
    assert usage.cache_write_tokens == 0
