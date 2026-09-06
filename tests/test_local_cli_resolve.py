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
    resolved = resolve_model("local/claude")

    assert isinstance(resolved, CliBridgeModel)
    assert isinstance(resolved._adapter, ClaudeCliAdapter)


@pytest.mark.parametrize(
    ("route", "adapter_type"),
    [
        ("local/codex", CodexCliAdapter),
        ("local/cursor", CursorCliAdapter),
    ],
)
def test_each_local_route_resolves_to_its_adapter(route: str, adapter_type: type) -> None:
    resolved = resolve_model(route)
    assert isinstance(resolved, CliBridgeModel)
    assert isinstance(resolved._adapter, adapter_type)


def test_adapters_registry_has_exactly_the_three_supported_clis() -> None:
    assert set(ADAPTERS) == {"claude", "codex", "cursor"}


def test_adapters_registry_matches_local_cli_routes() -> None:
    """The registry and the recognised routes must stay in lockstep.

    ``is_local_cli_model`` accepts anything in LOCAL_CLI_ROUTES, so a route added there
    without an adapter would pass validation and then fail at resolve time.
    """
    from valcore.settings import LOCAL_CLI_ROUTES

    assert {route.removeprefix("local/") for route in LOCAL_CLI_ROUTES} == set(ADAPTERS)
