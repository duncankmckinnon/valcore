"""Push valcore datasets to Logfire's hosted dataset store.

The only module that imports ``logfire.experimental``. That path exists only in a full
``logfire`` install (not the always-importable ``logfire_api`` shim) and is documented as
experimental, so the import is deferred to inside :func:`push_dataset` rather than taken at
module scope — importing this module must not require the ``logfire`` extra.

The upload runs inside an async FastAPI handler, so it uses ``AsyncLogfireAPIClient``: the sync
client would block the event loop for the whole upload. This module never reads or requires the
Logfire write token (``LOGFIRE_TOKEN``) — that credential belongs to tracing, not the datasets
API, which authenticates with a separate API key scoped to ``project:read_datasets`` /
``project:write_datasets``.
"""

from typing import Literal

from valcore import config
from valcore.errors import ConfigError, ContractError
from valcore.models import Dataset as VDataset
from valcore.models import DatasetRow
from valcore.spec import dataset_to_evals

_SET_KEY_COMMAND = "valcore config set-logfire-key"
_REQUIRED_SCOPES = ("project:read_datasets", "project:write_datasets")


def _resolve_api_key(api_key: str | None) -> str:
    """Resolve the Logfire API key from the argument, falling back to stored config.

    Raises :class:`ConfigError` naming both the CLI command to set the key and the two scopes it
    must carry — an under-scoped key otherwise fails with an authorization error that looks like
    a tracing misconfiguration rather than a missing datasets scope.
    """
    if api_key is not None:
        return api_key
    stored = config.load_config().logfire_api_key
    if stored is not None:
        return stored
    scopes = " and ".join(_REQUIRED_SCOPES)
    raise ConfigError(
        f"No Logfire API key configured. Run '{_SET_KEY_COMMAND}' with a key that carries "
        f"the {scopes} scopes."
    )


async def push_dataset(
    dataset: VDataset,
    rows: list[DatasetRow],
    *,
    api_key: str | None = None,
    name: str | None = None,
    description: str | None = None,
    on_conflict: Literal["update", "error"] = "update",
) -> dict:
    """Push ``dataset`` and ``rows`` to Logfire's hosted dataset store.

    Uses the async datasets client so the upload does not block the event loop of the FastAPI
    handler that calls this. Returns a plain dict built from the returned ``DatasetDetail``
    (a ``TypedDict`` whose only required keys are ``id`` and ``name``); absent optional keys
    become ``None`` rather than being omitted, so the shape is stable for callers. There is no
    URL field on ``DatasetDetail`` and none is synthesized here.
    """
    resolved_key = _resolve_api_key(api_key)

    try:
        from logfire.experimental.api_client import AsyncLogfireAPIClient
    except ImportError as exc:
        raise ConfigError(
            "The 'logfire' extra is required to push datasets to Logfire's hosted store."
        ) from exc

    evals_dataset = dataset_to_evals(dataset, rows, evaluators=[])
    client = AsyncLogfireAPIClient(api_key=resolved_key)
    try:
        detail = await client.push_dataset(
            evals_dataset,
            name=name,
            description=description,
            on_case_conflict=on_conflict,
        )
    except Exception as exc:
        raise ContractError(str(exc)) from exc

    return {
        "id": str(detail["id"]),
        "name": detail["name"],
        "case_count": detail.get("case_count"),
        "output_schema": detail.get("output_schema"),
    }
