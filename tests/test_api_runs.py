"""Tests for the runs API router: start, stream, results, cancel, retry, compare.

No network: agent behavior is injected via the ``get_agent_factory`` dependency using
``TestModel``/``FunctionModel`` agents, over a real ``Store`` on a ``tmp_path`` DB.
"""

import asyncio
import json

import httpx
import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from valcore.api.deps import get_store
from valcore.api.main import create_app
from valcore.api.routes.runs import get_agent_factory
from valcore.factory import build_output_model
from valcore.models import LabelSource, RunKind, RunStatus, ScoreKind
from valcore.store import Store, create_engine, init_db

CATEGORICAL_SCHEMA = {"kind": "categorical", "labels": ["pass", "fail"]}

VERSION_FIELDS = {
    "version_name": "v1",
    "model": "gateway/anthropic:claude-sonnet-5",
    "instructions": "Judge the row.",
    "prompt_template": "Input: {input} Output: {output}",
    "required_columns": ["input", "output"],
    "output_fields": [
        {
            "name": "verdict",
            "type": "enum",
            "description": "pass or fail",
            "enum_values": ["pass", "fail"],
        }
    ],
    "score_field": "verdict",
    "score_kind": ScoreKind.CATEGORICAL,
    "score_labels": ["pass", "fail"],
}

_TERMINAL = {
    RunStatus.COMPLETED.value,
    RunStatus.COMPLETED_WITH_ERRORS.value,
    RunStatus.CANCELLED.value,
    RunStatus.FAILED.value,
}


# -- Fixtures & helpers -------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> Store:
    """A fresh file-backed store isolated per test."""
    engine = create_engine(tmp_path / "runs.db")
    init_db(engine)
    return Store(engine)


@pytest.fixture(autouse=True)
def _gateway_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Present a gateway key by default.

    Launching a run now guards on ``config.require_gateway_key()`` before ever reaching
    ``execute_run``, regardless of whether a test injects its own agent via the
    ``get_agent_factory`` override. Without this, every pre-existing run test below would fail
    on the guard before its injected agent ever ran. The test that targets the guard itself
    clears the key explicitly.
    """
    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_API_KEY", "sk-test-gateway-key")


def _client(store: Store, agent_factory) -> httpx.AsyncClient:
    """Build an ASGI client with the store and agent-factory dependencies overridden."""
    app = create_app()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_agent_factory] = lambda: agent_factory
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def make_version(store: Store, **overrides):
    """Create an evaluator and a valid version, returning the version."""
    evaluator = store.create_evaluator("ev")
    return store.create_version(evaluator.id, **{**VERSION_FIELDS, **overrides})


def make_dataset(store: Store, labels: list[str | None], *, inputs: list[str] | None = None):
    """Create a dataset with one row per label entry (None = unlabeled)."""
    dataset = store.create_dataset("ds", "", ["input", "output"], CATEGORICAL_SCHEMA)
    inputs = inputs if inputs is not None else [f"in{i}" for i in range(len(labels))]
    rows = store.add_rows(
        dataset.id, [{"input": inp, "output": f"out{i}"} for i, inp in enumerate(inputs)]
    )
    for row, label in zip(rows, labels, strict=True):
        if label is not None:
            store.set_label(row.id, {"value": label}, LabelSource.MANUAL)
    return dataset, rows


def constant_factory(verdict: str = "pass"):
    """An agent factory whose model always emits ``verdict`` via the output tool."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"verdict": verdict})])

    return lambda version: Agent(FunctionModel(respond), output_type=build_output_model(version))


def slow_factory(delay: float = 0.1):
    """An agent factory whose model sleeps before emitting ``pass`` — for timing tests."""

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(delay)
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"verdict": "pass"})])

    return lambda version: Agent(FunctionModel(respond), output_type=build_output_model(version))


async def _poll_until_terminal(
    client: httpx.AsyncClient, run_id: str, timeout: float = 5.0
) -> dict:
    """Poll ``GET /api/runs/{id}`` until the run reaches a terminal status."""
    async with asyncio.timeout(timeout):
        while True:
            body = (await client.get(f"/api/runs/{run_id}")).json()
            if body["status"] in _TERMINAL:
                return body
            await asyncio.sleep(0.02)


async def _start_run(client: httpx.AsyncClient, version_id: str, dataset_id: str, **extra) -> dict:
    """POST a run and return the immediate response body."""
    resp = await client.post(
        "/api/runs",
        json={"kind": "validation", "version_id": version_id, "dataset_id": dataset_id, **extra},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# -- Start & completion -------------------------------------------------------


@pytest.mark.anyio
async def test_post_returns_pending_and_completes_in_background(store: Store) -> None:
    version = make_version(store)
    dataset, _ = make_dataset(store, ["pass", "fail", "pass"])
    factory = lambda v: Agent(TestModel(), output_type=build_output_model(v))

    async with _client(store, factory) as client:
        body = await _start_run(client, version.id, dataset.id)
        assert body["status"] == RunStatus.PENDING.value
        final = await _poll_until_terminal(client, body["id"])

    assert final["status"] in {
        RunStatus.COMPLETED.value,
        RunStatus.COMPLETED_WITH_ERRORS.value,
    }
    assert len(store.list_results(body["id"])) == 3


@pytest.mark.anyio
async def test_list_runs_filters_by_dataset(store: Store) -> None:
    version = make_version(store)
    ds_a, _ = make_dataset(store, ["pass"])
    ds_b, _ = make_dataset(store, ["pass"])

    async with _client(store, constant_factory()) as client:
        a = await _start_run(client, version.id, ds_a.id)
        b = await _start_run(client, version.id, ds_b.id)
        await _poll_until_terminal(client, a["id"])
        await _poll_until_terminal(client, b["id"])

        listed = (await client.get("/api/runs", params={"dataset_id": ds_a.id})).json()
    assert [r["id"] for r in listed] == [a["id"]]


# -- Results filtering --------------------------------------------------------


@pytest.mark.anyio
async def test_results_filter_disagreements_and_errors(store: Store) -> None:
    version = make_version(store)
    # Row 0 agrees (pass==pass), row 1 disagrees (pass!=fail), row 2 errors on "BOOM".
    dataset, rows = make_dataset(store, ["pass", "fail", "pass"], inputs=["ok0", "ok1", "BOOM"])

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if "BOOM" in str(messages):
            raise RuntimeError("kaboom")
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"verdict": "pass"})])

    factory = lambda v: Agent(FunctionModel(respond), output_type=build_output_model(v))

    async with _client(store, factory) as client:
        body = await _start_run(client, version.id, dataset.id)
        await _poll_until_terminal(client, body["id"])
        run_id = body["id"]

        disagreements = (
            await client.get(f"/api/runs/{run_id}/results", params={"only_disagreements": True})
        ).json()
        errors = (
            await client.get(f"/api/runs/{run_id}/results", params={"only_errors": True})
        ).json()
        all_rows = (await client.get(f"/api/runs/{run_id}/results")).json()

    assert all_rows["total"] == 3
    assert disagreements["total"] == 1
    assert disagreements["results"][0]["row_id"] == rows[1].id
    assert disagreements["results"][0]["label"] == "fail"
    assert errors["total"] == 1
    assert errors["results"][0]["row_id"] == rows[2].id
    assert errors["results"][0]["error"] is not None


@pytest.mark.anyio
async def test_results_pagination(store: Store) -> None:
    version = make_version(store)
    dataset, _ = make_dataset(store, ["pass"] * 5)

    async with _client(store, constant_factory()) as client:
        body = await _start_run(client, version.id, dataset.id)
        await _poll_until_terminal(client, body["id"])
        page = (
            await client.get(f"/api/runs/{body['id']}/results", params={"limit": 2, "offset": 1})
        ).json()

    assert page["total"] == 5
    assert page["limit"] == 2
    assert page["offset"] == 1
    assert len(page["results"]) == 2


# -- SSE ----------------------------------------------------------------------


async def _collect_sse_types(
    client: httpx.AsyncClient, run_id: str, timeout: float = 10.0
) -> list[str]:
    """Read the SSE stream, returning event names seen until ``finished``."""
    seen: list[str] = []
    async with asyncio.timeout(timeout):
        async with client.stream("GET", f"/api/runs/{run_id}/events") as resp:
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                line = line.strip()
                if line.startswith("event:"):
                    name = line[len("event:") :].strip()
                    seen.append(name)
                    if name in ("finished", "error"):
                        return seen
    return seen


@pytest.mark.anyio
async def test_sse_streams_row_progress_and_finished(store: Store) -> None:
    """A subscriber sees per-row progress and a terminal event.

    Whether the stream opens with ``started`` or with a replayed ``status`` is a race
    between the subscribe and the run's first event, so it is not asserted here — the
    replay path is what ``test_sse_late_subscriber_gets_replayed_status`` covers.
    """
    version = make_version(store)
    dataset, _ = make_dataset(store, ["pass", "pass", "pass"])

    async with _client(store, slow_factory(0.1)) as client:
        body = await _start_run(client, version.id, dataset.id, concurrency=1)
        seen = await _collect_sse_types(client, body["id"])

    assert "row" in seen
    assert "finished" in seen


@pytest.mark.anyio
async def test_sse_late_subscriber_gets_replayed_status(store: Store) -> None:
    version = make_version(store)
    dataset, _ = make_dataset(store, ["pass", "pass", "pass"])

    async with _client(store, constant_factory()) as client:
        body = await _start_run(client, version.id, dataset.id)
        final = await _poll_until_terminal(client, body["id"])

        # Connect only after the run has finished: the replay must not be stuck at 0%.
        first_event: dict = {}
        async with asyncio.timeout(10.0):
            async with client.stream("GET", f"/api/runs/{body['id']}/events") as resp:
                event_name = None
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if line.startswith("event:"):
                        event_name = line[len("event:") :].strip()
                    elif line.startswith("data:") and event_name == "status":
                        first_event = json.loads(line[len("data:") :].strip())
                        break

    assert first_event["status"] == final["status"]
    assert first_event["completed"] == 3


# -- Cancel -------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancel_transitions_to_cancelled(store: Store) -> None:
    version = make_version(store)
    dataset, _ = make_dataset(store, ["pass"] * 6)

    async with _client(store, slow_factory(0.1)) as client:
        body = await _start_run(client, version.id, dataset.id, concurrency=1)
        run_id = body["id"]

        # Wait until the run is actually running, then cancel mid-flight.
        async with asyncio.timeout(5.0):
            while (await client.get(f"/api/runs/{run_id}")).json()["status"] not in (
                RunStatus.RUNNING.value,
                *(_TERMINAL),
            ):
                await asyncio.sleep(0.01)

        cancel = await client.post(f"/api/runs/{run_id}/cancel")
        assert cancel.status_code == 200
        assert cancel.json()["cancel_requested"] is True

        final = await _poll_until_terminal(client, run_id)

    assert final["status"] == RunStatus.CANCELLED.value
    assert len(store.list_results(run_id)) < 6


# -- Retry failed -------------------------------------------------------------


@pytest.mark.anyio
async def test_retry_failed_reruns_only_failed_rows(store: Store) -> None:
    version = make_version(store)
    dataset, rows = make_dataset(store, ["pass", "pass", "pass"], inputs=["ok0", "BOOM", "ok2"])

    state = {"fail_boom": True}

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if state["fail_boom"] and "BOOM" in str(messages):
            raise RuntimeError("kaboom")
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"verdict": "pass"})])

    factory = lambda v: Agent(FunctionModel(respond), output_type=build_output_model(v))

    async with _client(store, factory) as client:
        body = await _start_run(client, version.id, dataset.id, concurrency=1)
        run_id = body["id"]
        first = await _poll_until_terminal(client, run_id)
        assert first["status"] == RunStatus.COMPLETED_WITH_ERRORS.value

        failed_ids = store.failed_result_row_ids(run_id)
        assert failed_ids == [rows[1].id]

        # Result ids of the rows that did NOT fail must survive the retry unchanged.
        surviving = {r.row_id: r.id for r in store.list_results(run_id) if r.row_id != rows[1].id}

        state["fail_boom"] = False
        retry = await client.post(f"/api/runs/{run_id}/retry-failed")
        assert retry.status_code == 200
        final = await _poll_until_terminal(client, run_id)

    assert final["status"] == RunStatus.COMPLETED.value
    assert store.failed_result_row_ids(run_id) == []
    after = {r.row_id: r.id for r in store.list_results(run_id) if r.row_id != rows[1].id}
    assert after == surviving  # only the failed row was re-executed


# -- Compare ------------------------------------------------------------------


def _seed_run_with_scores(store: Store, dataset_id: str, version_id: str, scores: dict[str, str]):
    """Create a completed run and persist a result per (row_id -> score)."""
    run = store.create_run(RunKind.VALIDATION, version_id, dataset_id, concurrency=1)
    for row_id, score in scores.items():
        store.add_result(run.id, row_id=row_id, output={"verdict": score}, score_value=score)
    store.update_run_status(run.id, RunStatus.COMPLETED)
    return run


@pytest.mark.anyio
async def test_compare_rejects_mismatched_datasets(store: Store) -> None:
    version = make_version(store)
    ds_a, rows_a = make_dataset(store, ["pass"])
    ds_b, rows_b = make_dataset(store, ["pass"])
    run_a = _seed_run_with_scores(store, ds_a.id, version.id, {rows_a[0].id: "pass"})
    run_b = _seed_run_with_scores(store, ds_b.id, version.id, {rows_b[0].id: "pass"})

    async with _client(store, constant_factory()) as client:
        resp = await client.get("/api/runs/compare", params={"a": run_a.id, "b": run_b.id})

    assert resp.status_code == 422


@pytest.mark.anyio
async def test_compare_orders_disagreements_first(store: Store) -> None:
    version = make_version(store)
    dataset, rows = make_dataset(store, ["pass", "fail", "pass"])
    run_a = _seed_run_with_scores(store, dataset.id, version.id, {r.id: "pass" for r in rows})
    # Run B disagrees with A only on the middle row.
    run_b = _seed_run_with_scores(
        store,
        dataset.id,
        version.id,
        {rows[0].id: "pass", rows[1].id: "fail", rows[2].id: "pass"},
    )

    async with _client(store, constant_factory()) as client:
        resp = await client.get("/api/runs/compare", params={"a": run_a.id, "b": run_b.id})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_a"]["id"] == run_a.id
    assert body["run_b"]["id"] == run_b.id
    assert body["rows"][0]["row_id"] == rows[1].id
    assert body["rows"][0]["disagree"] is True
    assert body["rows"][0]["score_a"] == "pass"
    assert body["rows"][0]["score_b"] == "fail"
    assert all(not row["disagree"] for row in body["rows"][1:])


# -- Background failure --------------------------------------------------------


@pytest.mark.anyio
async def test_background_task_failure_marks_run_failed(store: Store) -> None:
    version = make_version(store)
    # A validation run over a partially labeled dataset makes the runner raise
    # ContractError; the background wrapper must record it as FAILED.
    dataset, _ = make_dataset(store, ["pass", None, "fail"])

    async with _client(store, constant_factory()) as client:
        body = await _start_run(client, version.id, dataset.id)
        final = await _poll_until_terminal(client, body["id"])

    assert final["status"] == RunStatus.FAILED.value
    assert final["error"]


# -- Gateway guard --------------------------------------------------------------
#
# defer_model_check=True lets build_agent succeed with no gateway key, so today's failure
# lands deep inside runner._score_row and is recorded as one error per row (20 rows -> 20
# failed results, COMPLETED_WITH_ERRORS). The guard must instead sit before execute_run so a
# keyless run fails cleanly at setup: one error, zero RunResult rows.


@pytest.mark.anyio
async def test_run_without_gateway_key_fails_cleanly_with_no_results(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PYDANTIC_AI_GATEWAY_API_KEY", raising=False)
    version = make_version(store)
    dataset, _ = make_dataset(store, ["pass", "fail", "pass"])

    async with _client(store, constant_factory()) as client:
        body = await _start_run(client, version.id, dataset.id)
        final = await _poll_until_terminal(client, body["id"])

    assert final["status"] == RunStatus.FAILED.value
    assert final["error"]
    assert "valcore config set-key" in final["error"]
    # Exactly one clear setup failure, not one failed result per row.
    assert store.list_results(final["id"]) == []
