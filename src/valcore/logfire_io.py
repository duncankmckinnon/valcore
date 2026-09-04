"""Talk to Logfire's hosted dataset store.

The only module that imports ``logfire.experimental``. That path exists only in a full
``logfire`` install (not the always-importable ``logfire_api`` shim) and is documented as
experimental, so the import is deferred to inside the functions that need it rather than
taken at module scope — importing this module must not require the ``logfire`` extra.

Calls run inside an async FastAPI handler, so they use ``AsyncLogfireAPIClient``: the sync
client would block the event loop. This module never reads or requires the Logfire write
token (``LOGFIRE_TOKEN``) — that credential belongs to tracing.

Push authenticates with a write-key for the valcore project, scoped to
``project:read_datasets`` / ``project:write_datasets``. List and fetch authenticate with
the read key for the source project, which needs ``project:read_datasets`` (SQL pull still
uses ``project:read`` separately).
"""

from typing import Any, Literal

from pydantic import BaseModel
from pydantic_evals import Dataset as EvalsDataset
from pydantic_evals.dataset import Case

from valcore import config
from valcore.errors import ConfigError, ContractError
from valcore.models import Dataset as VDataset
from valcore.models import DatasetRow
from valcore.spec import dataset_to_evals, evals_to_dataset_fields

_SET_WRITE_KEY_COMMAND = "valcore config set-logfire-write-key"
_SET_READ_KEY_COMMAND = "valcore config set-logfire-read-key"
_WRITE_SCOPES = ("project:read_datasets", "project:write_datasets")
_READ_DATASETS_SCOPE = "project:read_datasets"


class HostedFetch(BaseModel):
    """A hosted dataset mapped into the fields ``Store.add_prepared_rows`` already accepts."""

    source_name: str
    name: str
    columns: list[str]
    label_schema: dict
    prepared: list[dict]


def _import_async_client() -> Any:
    """Import the async datasets client, or name the ``logfire`` extra if it is missing."""
    try:
        from logfire.experimental.api_client import AsyncLogfireAPIClient
    except ImportError as exc:
        raise ConfigError(
            "The 'logfire' extra is required to use Logfire's hosted dataset store."
        ) from exc
    return AsyncLogfireAPIClient


def _resolve_write_key(api_key: str | None) -> str:
    """Resolve the write key from the argument, falling back to stored config.

    Raises :class:`ConfigError` naming both the CLI command to set the key and the two scopes it
    must carry — an under-scoped key otherwise fails with an authorization error that looks like
    a tracing misconfiguration rather than a missing datasets scope.
    """
    if api_key is not None:
        return api_key
    stored = config.resolve_logfire_write_key(config.load_config())
    if stored is not None:
        return stored
    scopes = " and ".join(_WRITE_SCOPES)
    raise ConfigError(
        f"No Logfire API key configured. Run '{_SET_WRITE_KEY_COMMAND}' with a key that carries "
        f"the {scopes} scopes."
    )


def _resolve_read_key(api_key: str | None) -> str:
    """Resolve the read key from the argument, falling back to stored config.

    Hosted list/fetch need ``project:read_datasets`` on the source project. The write key is
    a different project and must not be used as a fallback.
    """
    if api_key is not None:
        return api_key
    stored = config.resolve_logfire_read_key(config.load_config())
    if stored is not None:
        return stored
    raise ConfigError(
        f"No Logfire API key configured. Run '{_SET_READ_KEY_COMMAND}' with a key that carries "
        f"the {_READ_DATASETS_SCOPE} scope."
    )


def _summary_dict(summary: dict) -> dict:
    """Normalise a ``DatasetSummary`` into a stable dict for API and CLI callers."""
    return {
        "id": str(summary["id"]),
        "name": summary["name"],
        "description": summary.get("description"),
        "case_count": summary.get("case_count"),
    }


def _exported_to_evals(exported: dict, fallback_name: str) -> EvalsDataset:
    """Build a pydantic-evals dataset from a raw hosted export, skipping stored evaluators.

    Hosted datasets may carry evaluator classes this process does not have. Reconstructing
    cases by hand keeps import from depending on those classes.
    """
    cases = [
        Case(
            name=item.get("name"),
            inputs=item.get("inputs"),
            expected_output=item.get("expected_output"),
            metadata=item.get("metadata") or {},
        )
        for item in exported.get("cases") or []
    ]
    local_name = exported.get("name") or fallback_name
    return EvalsDataset[dict[str, Any], object, dict[str, Any]](name=local_name, cases=cases)


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
    resolved_key = _resolve_write_key(api_key)
    AsyncLogfireAPIClient = _import_async_client()

    # wrap_output: the hosted API types ``expected_output`` as a dictionary and rejects a scalar
    # with ``dict_type: Input should be a valid dictionary``, so each label goes up as
    # ``{"value": label}``. Exported ``.dataset.json`` files keep the scalar form.
    evals_dataset = dataset_to_evals(dataset, rows, evaluators=[], wrap_output=True)
    try:
        async with AsyncLogfireAPIClient(api_key=resolved_key) as client:
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


async def list_hosted_datasets(*, api_key: str | None = None) -> list[dict]:
    """List hosted datasets in the project the read key is scoped to."""
    resolved_key = _resolve_read_key(api_key)
    AsyncLogfireAPIClient = _import_async_client()
    try:
        async with AsyncLogfireAPIClient(api_key=resolved_key) as client:
            summaries = await client.list_datasets()
    except Exception as exc:
        raise ContractError(str(exc)) from exc
    return [_summary_dict(summary) for summary in summaries]


async def fetch_hosted_dataset(id_or_name: str, *, api_key: str | None = None) -> HostedFetch:
    """Fetch one hosted dataset and map it into valcore dataset fields.

    Calls ``get_dataset`` without type arguments so the response stays a raw export dict.
    Typed parsing would try to deserialize evaluator classes this process may not have.
    """
    resolved_key = _resolve_read_key(api_key)
    AsyncLogfireAPIClient = _import_async_client()
    try:
        async with AsyncLogfireAPIClient(api_key=resolved_key) as client:
            exported = await client.get_dataset(id_or_name)
    except Exception as exc:
        raise ContractError(str(exc)) from exc

    evals_dataset = _exported_to_evals(exported, id_or_name)
    name, columns, label_schema, prepared = evals_to_dataset_fields(evals_dataset, None)
    return HostedFetch(
        source_name=id_or_name,
        name=name or id_or_name,
        columns=columns,
        label_schema=label_schema,
        prepared=prepared,
    )
