"""Tests for pushing datasets to Logfire's hosted store.

Pins ``logfire_io.push_dataset``'s behavior: API key resolution (argument over config), the
``on_conflict`` -> ``on_case_conflict`` rename, the ``DatasetDetail`` -> dict shape (absent
optional keys become ``None``, no URL field), that a client failure surfaces as
``ContractError``, that the client's async context manager is entered and exited on both success
and failure (so its underlying ``httpx.AsyncClient`` is never leaked), and -- the one no
functional test would otherwise catch -- that it is the *async* client, not the blocking one,
that gets constructed and awaited.

Every test stubs ``logfire.experimental.api_client`` by name so nothing here makes a network
call, needs a real API key, or requires the ``logfire`` extra to be installed.
"""

import inspect
import sys
import types
import uuid
from dataclasses import dataclass, field
from typing import Self

import pytest
from pydantic import TypeAdapter

from valcore.config import FileConfig, save_config
from valcore.errors import ConfigError, ContractError
from valcore.models import Dataset as VDataset
from valcore.models import DatasetRow, LabelSource


def make_rows() -> list[DatasetRow]:
    """One row labeled with a value from the dataset's categorical label space."""
    return [
        DatasetRow(
            dataset_id="d1",
            idx=0,
            data={"question": "Q1", "answer": "A1"},
            label={"value": "a"},
            label_source=LabelSource.MANUAL,
        ),
    ]


def make_dataset() -> VDataset:
    """A categorical dataset whose label enum must survive into the pushed schema."""
    return VDataset(
        name="refusal-quality",
        columns=["question", "answer"],
        label_schema={"kind": "categorical", "labels": ["a", "b"]},
    )


@dataclass
class _Recorder:
    """Captures what ``push_dataset`` does to the stubbed ``AsyncLogfireAPIClient``."""

    calls: list[dict] = field(default_factory=list)
    detail: dict = field(default_factory=lambda: {"id": uuid.uuid4(), "name": "pushed"})
    error: Exception | None = None
    sync_client_constructed: bool = False
    entered: bool = False
    exited: bool = False
    exited_with_exc: bool = False


def _install_stub_client(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    """Replace ``logfire.experimental.api_client`` with a stub module in ``sys.modules``.

    The real ``push_dataset`` must import that module lazily, inside the function body, so
    inserting a fake module under its dotted name intercepts the import without needing a real
    ``logfire`` install or any network access.
    """

    class StubAsyncClient:
        def __init__(self, api_key: str | None = None) -> None:
            self.api_key = api_key

        async def __aenter__(self) -> Self:
            recorder.entered = True
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
            recorder.exited = True
            recorder.exited_with_exc = exc_type is not None
            return False

        async def push_dataset(
            self,
            dataset: object,
            *,
            name: str | None = None,
            description: str | None = None,
            on_case_conflict: str = "update",
        ) -> dict:
            recorder.calls.append(
                {
                    "dataset": dataset,
                    "name": name,
                    "description": description,
                    "on_case_conflict": on_case_conflict,
                    "api_key": self.api_key,
                }
            )
            if recorder.error is not None:
                raise recorder.error
            return recorder.detail

    class StubSyncClient:
        """Stands in for the blocking ``LogfireAPIClient``; must never be constructed."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            recorder.sync_client_constructed = True
            raise AssertionError("sync LogfireAPIClient must never be constructed")

    fake_module = types.ModuleType("logfire.experimental.api_client")
    fake_module.AsyncLogfireAPIClient = StubAsyncClient  # type: ignore[attr-defined]
    fake_module.LogfireAPIClient = StubSyncClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "logfire.experimental.api_client", fake_module)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    """A fresh recorder with the stub client installed for this test only."""
    rec = _Recorder()
    _install_stub_client(monkeypatch, rec)
    return rec


# --- API key resolution --------------------------------------------------------


@pytest.mark.anyio
async def test_missing_api_key_raises_config_error_naming_command_and_scopes(
    recorder: _Recorder,
) -> None:
    from valcore.logfire_io import push_dataset

    with pytest.raises(ConfigError) as exc:
        await push_dataset(make_dataset(), make_rows())
    message = str(exc.value)
    assert "valcore config set-logfire-key" in message
    assert "project:read_datasets" in message
    assert "project:write_datasets" in message
    assert recorder.calls == []


@pytest.mark.anyio
async def test_api_key_argument_wins_over_config(recorder: _Recorder) -> None:
    from valcore.logfire_io import push_dataset

    save_config(FileConfig(logfire_api_key="lf-from-file"))
    await push_dataset(make_dataset(), make_rows(), api_key="lf-from-arg")
    assert recorder.calls[0]["api_key"] == "lf-from-arg"


@pytest.mark.anyio
async def test_api_key_falls_back_to_config_when_argument_is_none(recorder: _Recorder) -> None:
    from valcore.logfire_io import push_dataset

    save_config(FileConfig(logfire_api_key="lf-from-file"))
    await push_dataset(make_dataset(), make_rows(), api_key=None)
    assert recorder.calls[0]["api_key"] == "lf-from-file"


# --- generics fix is observable here -------------------------------------------


@pytest.mark.anyio
async def test_pushed_dataset_output_schema_is_the_label_enum_not_empty(
    recorder: _Recorder,
) -> None:
    """Pins the point of the spec-generics fix: a bare ``object`` OutputT infers to ``{}``."""
    from valcore.logfire_io import push_dataset

    save_config(FileConfig(logfire_api_key="lf-key"))
    await push_dataset(make_dataset(), make_rows())

    pushed = recorder.calls[0]["dataset"]
    output_type = pushed.__class__.__pydantic_generic_metadata__["args"][1]
    schema = TypeAdapter(output_type).json_schema()
    assert schema == {"enum": ["a", "b"], "type": "string"}
    assert schema != {}
    assert pushed.evaluators == []


# --- on_conflict -> on_case_conflict rename ------------------------------------


@pytest.mark.anyio
async def test_on_conflict_is_threaded_through_as_on_case_conflict(recorder: _Recorder) -> None:
    from valcore.logfire_io import push_dataset

    save_config(FileConfig(logfire_api_key="lf-key"))
    await push_dataset(make_dataset(), make_rows(), on_conflict="error")
    assert recorder.calls[0]["on_case_conflict"] == "error"


@pytest.mark.anyio
async def test_on_conflict_defaults_to_update(recorder: _Recorder) -> None:
    from valcore.logfire_io import push_dataset

    save_config(FileConfig(logfire_api_key="lf-key"))
    await push_dataset(make_dataset(), make_rows())
    assert recorder.calls[0]["on_case_conflict"] == "update"


# --- name / description passthrough --------------------------------------------


@pytest.mark.anyio
async def test_name_and_description_are_passed_through(recorder: _Recorder) -> None:
    from valcore.logfire_io import push_dataset

    save_config(FileConfig(logfire_api_key="lf-key"))
    await push_dataset(make_dataset(), make_rows(), name="custom-name", description="custom-desc")
    assert recorder.calls[0]["name"] == "custom-name"
    assert recorder.calls[0]["description"] == "custom-desc"


# --- DatasetDetail -> dict shape -------------------------------------------------


@pytest.mark.anyio
async def test_result_dict_has_none_for_absent_optional_fields(recorder: _Recorder) -> None:
    from valcore.logfire_io import push_dataset

    save_config(FileConfig(logfire_api_key="lf-key"))
    detail_id = uuid.uuid4()
    recorder.detail = {"id": detail_id, "name": "pushed-dataset"}

    result = await push_dataset(make_dataset(), make_rows())

    assert result == {
        "id": str(detail_id),
        "name": "pushed-dataset",
        "case_count": None,
        "output_schema": None,
    }
    assert isinstance(result["id"], str)


@pytest.mark.anyio
async def test_result_dict_carries_present_optional_fields(recorder: _Recorder) -> None:
    from valcore.logfire_io import push_dataset

    save_config(FileConfig(logfire_api_key="lf-key"))
    recorder.detail = {
        "id": uuid.uuid4(),
        "name": "pushed-dataset",
        "case_count": 3,
        "output_schema": {"type": "string"},
    }

    result = await push_dataset(make_dataset(), make_rows())

    assert result["case_count"] == 3
    assert result["output_schema"] == {"type": "string"}


@pytest.mark.anyio
async def test_result_never_has_a_url_field(recorder: _Recorder) -> None:
    """``DatasetDetail`` has no URL field; ``push_dataset`` must never synthesize one."""
    from valcore.logfire_io import push_dataset

    save_config(FileConfig(logfire_api_key="lf-key"))
    result = await push_dataset(make_dataset(), make_rows())
    assert "url" not in result


# --- client errors surface as ContractError --------------------------------------


@pytest.mark.anyio
async def test_client_exception_surfaces_as_contract_error(recorder: _Recorder) -> None:
    from valcore.logfire_io import push_dataset

    save_config(FileConfig(logfire_api_key="lf-key"))
    recorder.error = RuntimeError("under-scoped API key")

    with pytest.raises(ContractError, match="under-scoped API key"):
        await push_dataset(make_dataset(), make_rows())


# --- client context manager is always entered and exited --------------------------


@pytest.mark.anyio
async def test_client_context_manager_is_entered_and_exited_on_success(
    recorder: _Recorder,
) -> None:
    """The client owns an ``httpx.AsyncClient``; its context manager must close it."""
    from valcore.logfire_io import push_dataset

    save_config(FileConfig(logfire_api_key="lf-key"))
    await push_dataset(make_dataset(), make_rows())

    assert recorder.entered is True
    assert recorder.exited is True
    assert recorder.exited_with_exc is False


@pytest.mark.anyio
async def test_client_context_manager_is_exited_on_failure(recorder: _Recorder) -> None:
    """Cleanup must still happen when the upload itself raises, not just on success."""
    from valcore.logfire_io import push_dataset

    save_config(FileConfig(logfire_api_key="lf-key"))
    recorder.error = RuntimeError("boom")

    with pytest.raises(ContractError):
        await push_dataset(make_dataset(), make_rows())

    assert recorder.entered is True
    assert recorder.exited is True
    assert recorder.exited_with_exc is True


# --- missing `logfire` extra -------------------------------------------------------


@pytest.mark.anyio
async def test_missing_logfire_extra_raises_config_error_naming_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate the ``logfire`` extra being absent by poisoning its import.

    Setting a module to ``None`` in ``sys.modules`` is the standard way to force
    ``ImportError`` on a subsequent ``import``/``from ... import`` of that dotted name, without
    needing to actually uninstall anything.
    """
    from valcore.logfire_io import push_dataset

    monkeypatch.setitem(sys.modules, "logfire.experimental.api_client", None)
    save_config(FileConfig(logfire_api_key="lf-key"))

    with pytest.raises(ConfigError, match="logfire"):
        await push_dataset(make_dataset(), make_rows())


# --- the async client, never the sync one, is used --------------------------------


@pytest.mark.anyio
async def test_uses_the_async_client_and_awaits_it(recorder: _Recorder) -> None:
    """Guards against swapping in the sync ``LogfireAPIClient``.

    That would block the event loop for the whole upload inside the async FastAPI handler that
    calls this -- a defect no purely functional test would catch, since the sync client would
    still return a usable result.
    """
    from valcore.logfire_io import push_dataset

    save_config(FileConfig(logfire_api_key="lf-key"))
    await push_dataset(make_dataset(), make_rows())

    # The stub's `push_dataset` body only runs -- appending to `recorder.calls` -- if it was
    # actually awaited; an un-awaited coroutine would leave this empty.
    assert len(recorder.calls) == 1
    assert recorder.sync_client_constructed is False

    stub_module = sys.modules["logfire.experimental.api_client"]
    assert inspect.iscoroutinefunction(stub_module.AsyncLogfireAPIClient.push_dataset)
    assert not inspect.iscoroutinefunction(stub_module.LogfireAPIClient.__init__)
