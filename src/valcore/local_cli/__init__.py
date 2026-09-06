"""Local-CLI model support: wrapping a locally installed agent CLI as a pydantic_ai Model.

resolve_model is the single seam factory.py, generator.py, and datagen.py route their
model string through instead of passing it straight to Agent(...): a gateway/... string
passes through unchanged, a local/<cli> string resolves to a CliBridgeModel.
"""

from valcore.errors import ConfigError
from valcore.local_cli.bridge_model import CliAdapter, CliBridgeModel
from valcore.local_cli.claude_adapter import ClaudeCliAdapter
from valcore.local_cli.codex_adapter import CodexCliAdapter
from valcore.local_cli.cursor_adapter import CursorCliAdapter
from valcore.settings import is_local_cli_model

ADAPTERS: dict[str, CliAdapter] = {
    "claude": ClaudeCliAdapter(),
    "codex": CodexCliAdapter(),
    "cursor": CursorCliAdapter(),
}


def resolve_model(model_string: str) -> str | CliBridgeModel:
    """Return `model_string` unchanged for a gateway route, or a CliBridgeModel for a local one."""
    if not is_local_cli_model(model_string):
        return model_string
    cli_name = model_string.removeprefix("local/")
    adapter = ADAPTERS.get(cli_name)
    if adapter is None:
        raise ConfigError(f"Unknown local CLI {cli_name!r}; valid names are {sorted(ADAPTERS)}.")
    return CliBridgeModel(adapter)
