"""Tests for resolve_model: the single seam factory.py/generator.py/datagen.py route through."""

import pytest

from valcore.local_cli import ADAPTERS, resolve_model
from valcore.local_cli.bridge_model import CliBridgeModel
from valcore.local_cli.claude_adapter import ClaudeCliAdapter
from valcore.local_cli.codex_adapter import CodexCliAdapter
from valcore.local_cli.cursor_adapter import CursorCliAdapter


def test_gateway_model_string_passes_through_unchanged() -> None:
    assert resolve_model("gateway/anthropic:claude-sonnet-5") == "gateway/anthropic:claude-sonnet-5"


def test_local_claude_model_resolves_to_a_cli_bridge_model() -> None:
    resolved = resolve_model("local/claude:sonnet")

    assert isinstance(resolved, CliBridgeModel)
    assert resolved.model_name == "sonnet"
    assert isinstance(resolved._adapter, ClaudeCliAdapter)


@pytest.mark.parametrize(
    ("route", "adapter_type"),
    [
        ("local/codex:gpt-5-codex", CodexCliAdapter),
        ("local/cursor:composer", CursorCliAdapter),
    ],
)
def test_each_local_route_resolves_to_its_adapter(route: str, adapter_type: type) -> None:
    resolved = resolve_model(route)
    assert isinstance(resolved, CliBridgeModel)
    assert isinstance(resolved._adapter, adapter_type)


def test_adapters_registry_has_exactly_the_three_supported_clis() -> None:
    assert set(ADAPTERS) == {"claude", "codex", "cursor"}
