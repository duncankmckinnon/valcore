"""Opt-in smoke test against a real, locally installed, authenticated `claude` CLI.

Skipped unless VALCORE_TEST_LIVE_CLI=claude is set -- this is never run in CI, only by
whoever is iterating on ClaudeCliAdapter and wants to confirm the real flags still work.
"""

import os

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent

from valcore.local_cli import resolve_model
from valcore.local_cli.bridge_model import CliBridgeModel

pytestmark = pytest.mark.skipif(
    os.environ.get("VALCORE_TEST_LIVE_CLI") != "claude",
    reason="set VALCORE_TEST_LIVE_CLI=claude to run this against a real installed claude CLI",
)


class Verdict(BaseModel):
    verdict: str


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_claude_cli_returns_structured_output() -> None:
    model = resolve_model("local/claude:sonnet")
    assert isinstance(model, CliBridgeModel)
    agent = Agent(model, output_type=Verdict, instructions="Always set verdict to 'ok'.")

    result = await agent.run("Say hello.")

    assert result.output.verdict == "ok"
