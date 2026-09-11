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
from valcore.models import Annotation, DatasetRow, LabelSet, LabelSource, ScoreKind
from valcore.models import Dataset as VDataset


def make_rows() -> list[DatasetRow]:
    """One row of data, with no annotation attached; tests attach one via a LabelSet as needed."""
    return [
        DatasetRow(
            dataset_id="d1",
            idx=0,
            data={"question": "Q1", "answer": "A1"},
        ),
    ]


def make_dataset() -> VDataset:
    """A dataset whose columns match the row data above."""
    return VDataset(name="refusal-quality", columns=["question", "answer"])


def make_label_set(**overrides: object) -> LabelSet:
    """A categorical label set whose label enum must survive into the pushed schema."""
    base: dict[str, object] = {
        "dataset_id": "d1",
        "name": "refusal-quality",
        "description": "",
        "kind": ScoreKind.CATEGORICAL,
        "labels": [{"name": "a", "description": "d"}, {"name": "b", "description": "d"}],
    }
    base.update(overrides)
    return LabelSet(**base)


def make_annotation(row: DatasetRow, label_set: LabelSet, **overrides: object) -> Annotation:
    """An annotation on ``row`` under ``label_set``, labeled with a value from its label space."""
    base: dict[str, object] = {
        "label_set_id": label_set.id,
        "dataset_row_id": row.id,
        "labels": ["a"],
        "source": LabelSource.MANUAL,
    }
    base.update(overrides)
    return Annotation(**base)


@dataclass
class _Recorder:
    """Captures what the datasets client does to the stubbed ``AsyncLogfireAPIClient``."""

    calls: list[dict] = field(default_factory=list)
    list_calls: list[dict] = field(default_factory=list)
    get_calls: list[dict] = field(default_factory=list)
    detail: dict = field(default_factory=lambda: {"id": uuid.uuid4(), "name": "pushed"})
    summaries: list[dict] = field(default_factory=list)
    exported: dict = field(default_factory=lambda: {"name": "stub", "cases": []})
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

        async def list_datasets(self) -> list[dict]:
            recorder.list_calls.append({"api_key": self.api_key})
            if recorder.error is not None:
                raise recorder.error
            return recorder.summaries

        async def get_dataset(
            self,
            id_or_name: str,
            input_type: object = None,
            output_type: object = None,
            metadata_type: object = None,
            *,
            include_cases: bool = True,
        ) -> dict:
            recorder.get_calls.append(
                {
                    "id_or_name": id_or_name,
                    "include_cases": include_cases,
                    "api_key": self.api_key,
                    "input_type": input_type,
                }
            )
            if recorder.error is not None:
                raise recorder.error
            if not include_cases:
                return recorder.detail
            return recorder.exported

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
    assert "valcore config set-logfire-write-key" in message
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
async def test_pushed_dataset_output_schema_is_an_object_carrying_the_label_enum(
    recorder: _Recorder,
) -> None:
    """Pins two things at once about the hosted expected-output schema.

    A bare ``object`` OutputT would infer to ``{}`` -- the point of the generics fix -- and the
    hosted API additionally requires an *object*, rejecting a scalar with
    ``dict_type: Input should be a valid dictionary``. So the schema must be an object whose
    ``value`` property carries the label enum, not the bare enum.
    """
    from valcore.logfire_io import push_dataset

    save_config(FileConfig(logfire_api_key="lf-key"))
    rows = make_rows()
    label_set = make_label_set()
    annotation = make_annotation(rows[0], label_set)
    await push_dataset(make_dataset(), rows, label_set=label_set, annotations=[annotation])

    pushed = recorder.calls[0]["dataset"]
    output_type = pushed.__class__.__pydantic_generic_metadata__["args"][1]
    schema = TypeAdapter(output_type).json_schema()
    assert schema != {}
    assert schema["type"] == "object"
    # pydantic adds a derived ``title``; assert on the parts that carry meaning.
    value_schema = schema["properties"]["value"]
    assert value_schema["type"] == "string"
    assert value_schema["enum"] == ["a", "b"]
    assert pushed.evaluators == []


@pytest.mark.anyio
async def test_pushed_cases_wrap_expected_output_in_a_dict(recorder: _Recorder) -> None:
    """The hosted API types ``expected_output`` as a dictionary; a scalar is a 422."""
    from valcore.logfire_io import push_dataset

    save_config(FileConfig(logfire_api_key="lf-key"))
    rows = make_rows()
    label_set = make_label_set()
    annotation = make_annotation(rows[0], label_set)
    await push_dataset(make_dataset(), rows, label_set=label_set, annotations=[annotation])

    for case in recorder.calls[0]["dataset"].cases:
        assert isinstance(case.expected_output, dict), case.expected_output
        assert set(case.expected_output) == {"value"}


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


# --- list / fetch hosted datasets (read key) ------------------------------------


def _qa_export() -> dict:
    """A hosted export whose labels are wrapped the way Logfire's API requires on push."""
    return {
        "name": "qa-set",
        "cases": [
            {
                "name": "c1",
                "inputs": {"question": "Q1"},
                "expected_output": {"value": "yes"},
            },
            {
                "name": "c2",
                "inputs": {"question": "Q2"},
            },
        ],
    }


@pytest.mark.anyio
async def test_list_hosted_datasets_missing_read_key_names_command_and_read_datasets_scope(
    recorder: _Recorder,
) -> None:
    from valcore.logfire_io import list_hosted_datasets

    save_config(FileConfig(logfire_write_key="lf-write"))
    with pytest.raises(ConfigError) as exc:
        await list_hosted_datasets()
    message = str(exc.value)
    assert "valcore config set-logfire-read-key" in message
    assert "project:read_datasets" in message
    assert recorder.list_calls == []


@pytest.mark.anyio
async def test_list_hosted_datasets_uses_the_read_key_not_the_write_key(
    recorder: _Recorder,
) -> None:
    from valcore.logfire_io import list_hosted_datasets

    save_config(FileConfig(logfire_read_key="lf-read", logfire_write_key="lf-write"))
    recorder.summaries = [{"id": uuid.uuid4(), "name": "qa-set"}]
    await list_hosted_datasets()
    assert recorder.list_calls[0]["api_key"] == "lf-read"


@pytest.mark.anyio
async def test_list_hosted_datasets_stringifies_ids_and_fills_absent_optional_fields(
    recorder: _Recorder,
) -> None:
    from valcore.logfire_io import list_hosted_datasets

    save_config(FileConfig(logfire_read_key="lf-read"))
    first_id = uuid.uuid4()
    recorder.summaries = [
        {"id": first_id, "name": "qa-set", "description": "Q&A", "case_count": 12},
        {"id": uuid.uuid4(), "name": "empty"},
    ]

    result = await list_hosted_datasets()

    assert result[0] == {
        "id": str(first_id),
        "name": "qa-set",
        "description": "Q&A",
        "case_count": 12,
    }
    assert result[1]["name"] == "empty"
    assert result[1]["description"] is None
    assert result[1]["case_count"] is None


@pytest.mark.anyio
async def test_fetch_hosted_dataset_missing_read_key_names_command_and_read_datasets_scope(
    recorder: _Recorder,
) -> None:
    from valcore.logfire_io import fetch_hosted_dataset

    with pytest.raises(ConfigError) as exc:
        await fetch_hosted_dataset("qa-set")
    message = str(exc.value)
    assert "valcore config set-logfire-read-key" in message
    assert "project:read_datasets" in message
    assert recorder.get_calls == []


@pytest.mark.anyio
async def test_fetch_hosted_dataset_uses_the_read_key_and_requests_cases(
    recorder: _Recorder,
) -> None:
    from valcore.logfire_io import fetch_hosted_dataset

    save_config(FileConfig(logfire_read_key="lf-read", logfire_write_key="lf-write"))
    recorder.exported = _qa_export()
    await fetch_hosted_dataset("qa-set")
    assert recorder.get_calls[0]["api_key"] == "lf-read"
    assert recorder.get_calls[0]["id_or_name"] == "qa-set"
    assert recorder.get_calls[0]["include_cases"] is True
    assert recorder.get_calls[0]["input_type"] is None
    assert recorder.sync_client_constructed is False


@pytest.mark.anyio
async def test_fetch_hosted_dataset_maps_cases_and_unwraps_hosted_labels(
    recorder: _Recorder,
) -> None:
    from valcore.logfire_io import fetch_hosted_dataset

    save_config(FileConfig(logfire_read_key="lf-read"))
    recorder.exported = _qa_export()

    result = await fetch_hosted_dataset("qa-set")

    assert result.source_name == "qa-set"
    assert result.name == "qa-set"
    assert result.columns == ["question"]
    assert result.label_schema == {"kind": "categorical", "labels": ["yes"]}
    assert result.prepared[0]["data"] == {"question": "Q1"}
    assert "label" not in result.prepared[0]
    assert result.row_annotations[0] is not None
    assert result.row_annotations[0]["labels"] == ["yes"]
    assert result.prepared[1]["data"] == {"question": "Q2"}
    assert result.row_annotations[1] is None


@pytest.mark.anyio
async def test_fetch_hosted_dataset_falls_back_to_the_requested_name_when_export_omits_it(
    recorder: _Recorder,
) -> None:
    from valcore.logfire_io import fetch_hosted_dataset

    save_config(FileConfig(logfire_read_key="lf-read"))
    recorder.exported = {"cases": [{"inputs": {"prompt": "hi"}}]}

    result = await fetch_hosted_dataset("source-name")

    assert result.name == "source-name"
    assert result.columns == ["prompt"]


@pytest.mark.anyio
async def test_fetch_hosted_dataset_client_exception_surfaces_as_contract_error(
    recorder: _Recorder,
) -> None:
    from valcore.logfire_io import fetch_hosted_dataset

    save_config(FileConfig(logfire_read_key="lf-read"))
    recorder.error = RuntimeError("dataset not found")

    with pytest.raises(ContractError, match="dataset not found"):
        await fetch_hosted_dataset("missing")


@pytest.mark.anyio
async def test_list_and_fetch_exit_the_client_on_success_and_failure(
    recorder: _Recorder,
) -> None:
    from valcore.logfire_io import fetch_hosted_dataset, list_hosted_datasets

    save_config(FileConfig(logfire_read_key="lf-read"))
    recorder.summaries = [{"id": uuid.uuid4(), "name": "qa-set"}]
    await list_hosted_datasets()
    assert recorder.entered is True
    assert recorder.exited is True

    recorder.entered = False
    recorder.exited = False
    recorder.error = RuntimeError("boom")
    with pytest.raises(ContractError):
        await fetch_hosted_dataset("qa-set")
    assert recorder.entered is True
    assert recorder.exited is True
    assert recorder.exited_with_exc is True
