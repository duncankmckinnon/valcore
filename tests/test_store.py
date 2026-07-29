"""Tests for the SQLite store layer against a real tmp_path database file."""

import threading
from pathlib import Path

import pytest

from evalcore.errors import FrozenVersionError, NotFoundError
from evalcore.models import (
    DatasetRow,
    EvaluatorVersion,
    LabelSource,
    RunKind,
    RunResult,
    RunStatus,
    ScoreKind,
)
from evalcore.store import Store, create_engine, init_db, session_scope


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """A Store backed by a real on-disk SQLite file (never in-memory)."""
    engine = create_engine(tmp_path / "eval.db")
    init_db(engine)
    return Store(engine)


VERSION_FIELDS: dict[str, object] = {
    "version_name": "v1",
    "model": "gateway/anthropic:claude-sonnet-5",
    "instructions": "You are an evaluator.",
    "prompt_template": "Rate the answer to {question}.",
    "required_columns": ["question"],
    "output_fields": [
        {
            "name": "verdict",
            "type": "enum",
            "description": "The verdict.",
            "enum_values": ["pass", "fail"],
        }
    ],
    "score_field": "verdict",
    "score_kind": ScoreKind.CATEGORICAL,
    "score_labels": ["pass", "fail"],
    "capabilities": [],
    "tools": [],
}

LABEL_SCHEMA: dict[str, object] = {"kind": "categorical", "labels": ["pass", "fail"]}


def version_fields(**overrides: object) -> dict[str, object]:
    """Return a copy of the valid version fields with overrides applied."""
    fields = dict(VERSION_FIELDS)
    fields["output_fields"] = [dict(f) for f in VERSION_FIELDS["output_fields"]]  # type: ignore[union-attr]
    fields.update(overrides)
    return fields


# -- Engine / schema ---------------------------------------------------------


def test_create_engine_enables_pragmas(tmp_path: Path) -> None:
    engine = create_engine(tmp_path / "pragma.db")
    init_db(engine)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"


def test_create_engine_uses_settings_db_path_when_none() -> None:
    engine = create_engine(None)
    assert "evalcore.db" in str(engine.url)


def test_session_scope_rolls_back_on_error(store: Store) -> None:
    evaluator = store.create_evaluator("keep")
    with pytest.raises(RuntimeError), session_scope(store.engine) as session:
        ev = session.get(type(evaluator), evaluator.id)
        ev.name = "changed"
        session.add(ev)
        raise RuntimeError("boom")
    # The change must not have persisted.
    assert store.get_evaluator(evaluator.id).name == "keep"


# -- Evaluators --------------------------------------------------------------


def test_evaluator_crud(store: Store) -> None:
    created = store.create_evaluator("my-eval", "a description")
    assert created.name == "my-eval"
    assert created.description == "a description"
    assert created.id

    fetched = store.get_evaluator(created.id)
    assert fetched.id == created.id
    assert fetched.name == "my-eval"

    listed = store.list_evaluators()
    assert [e.id for e in listed] == [created.id]

    store.delete_evaluator(created.id)
    assert store.list_evaluators() == []
    with pytest.raises(NotFoundError):
        store.get_evaluator(created.id)


def test_get_evaluator_missing_raises(store: Store) -> None:
    with pytest.raises(NotFoundError):
        store.get_evaluator("nope")


def test_delete_evaluator_missing_raises(store: Store) -> None:
    with pytest.raises(NotFoundError):
        store.delete_evaluator("nope")


# -- Versions ----------------------------------------------------------------


def test_create_version_sets_active_version_id(store: Store) -> None:
    evaluator = store.create_evaluator("e")
    version = store.create_version(evaluator.id, **version_fields())
    assert version.evaluator_id == evaluator.id
    assert store.get_evaluator(evaluator.id).active_version_id == version.id


def test_create_version_advances_active_pointer(store: Store) -> None:
    evaluator = store.create_evaluator("e")
    v1 = store.create_version(evaluator.id, **version_fields(version_name="v1"))
    v2 = store.create_version(evaluator.id, **version_fields(version_name="v2"))
    assert v1.id != v2.id
    assert store.get_evaluator(evaluator.id).active_version_id == v2.id
    listed = store.list_versions(evaluator.id)
    assert {v.id for v in listed} == {v1.id, v2.id}


def test_create_version_invalid_config_raises(store: Store) -> None:
    from evalcore.errors import ConfigError

    evaluator = store.create_evaluator("e")
    with pytest.raises(ConfigError):
        store.create_version(evaluator.id, **version_fields(output_fields=[]))
    # Nothing should have been persisted, and the pointer stays unset.
    assert store.list_versions(evaluator.id) == []
    assert store.get_evaluator(evaluator.id).active_version_id is None


def test_create_version_missing_evaluator_raises(store: Store) -> None:
    with pytest.raises(NotFoundError):
        store.create_version("nope", **version_fields())


def test_get_version_missing_raises(store: Store) -> None:
    with pytest.raises(NotFoundError):
        store.get_version("nope")


def test_update_version_mutates_fields(store: Store) -> None:
    evaluator = store.create_evaluator("e")
    version = store.create_version(evaluator.id, **version_fields())
    updated = store.update_version(version.id, notes="edited", version_name="renamed")
    assert updated.notes == "edited"
    assert updated.version_name == "renamed"
    assert store.get_version(version.id).notes == "edited"


def test_update_version_frozen_raises(store: Store) -> None:
    evaluator = store.create_evaluator("e")
    version = store.create_version(evaluator.id, **version_fields())
    store.freeze_version(version.id)
    with pytest.raises(FrozenVersionError):
        store.update_version(version.id, notes="cannot")
    assert store.get_version(version.id).notes == ""


def test_freeze_version_sets_flag(store: Store) -> None:
    evaluator = store.create_evaluator("e")
    version = store.create_version(evaluator.id, **version_fields())
    assert version.frozen is False
    frozen = store.freeze_version(version.id)
    assert frozen.frozen is True
    assert store.get_version(version.id).frozen is True


def test_freeze_version_missing_raises(store: Store) -> None:
    with pytest.raises(NotFoundError):
        store.freeze_version("nope")


# -- Datasets ----------------------------------------------------------------


def test_dataset_crud(store: Store) -> None:
    ds = store.create_dataset("d", "desc", ["question", "answer"], LABEL_SCHEMA)
    assert ds.columns == ["question", "answer"]
    assert ds.label_schema == LABEL_SCHEMA

    assert store.get_dataset(ds.id).id == ds.id
    assert [d.id for d in store.list_datasets()] == [ds.id]

    store.delete_dataset(ds.id)
    assert store.list_datasets() == []
    with pytest.raises(NotFoundError):
        store.get_dataset(ds.id)


def test_get_dataset_missing_raises(store: Store) -> None:
    with pytest.raises(NotFoundError):
        store.get_dataset("nope")


def test_add_rows_assigns_sequential_idx_across_calls(store: Store) -> None:
    ds = store.create_dataset("d", "", ["question"], LABEL_SCHEMA)
    first = store.add_rows(ds.id, [{"question": "a"}, {"question": "b"}])
    assert [r.idx for r in first] == [0, 1]

    second = store.add_rows(ds.id, [{"question": "c"}, {"question": "d"}, {"question": "e"}])
    assert [r.idx for r in second] == [2, 3, 4]

    rows = store.list_rows(ds.id)
    assert [r.idx for r in rows] == [0, 1, 2, 3, 4]
    assert [r.data["question"] for r in rows] == ["a", "b", "c", "d", "e"]


def test_add_rows_empty_first_call_starts_at_zero(store: Store) -> None:
    ds = store.create_dataset("d", "", ["question"], LABEL_SCHEMA)
    assert store.add_rows(ds.id, []) == []
    rows = store.add_rows(ds.id, [{"question": "a"}])
    assert [r.idx for r in rows] == [0]


def test_add_rows_missing_dataset_raises(store: Store) -> None:
    with pytest.raises(NotFoundError):
        store.add_rows("nope", [{"question": "a"}])


def test_set_label_and_labeled_count(store: Store) -> None:
    ds = store.create_dataset("d", "", ["question"], LABEL_SCHEMA)
    rows = store.add_rows(ds.id, [{"question": "a"}, {"question": "b"}, {"question": "c"}])

    assert store.labeled_count(ds.id) == (0, 3)

    labeled = store.set_label(rows[0].id, {"verdict": "pass"}, LabelSource.MANUAL, note="ok")
    assert labeled.label == {"verdict": "pass"}
    assert labeled.label_source is LabelSource.MANUAL
    assert labeled.note == "ok"
    assert store.labeled_count(ds.id) == (1, 3)

    store.set_label(rows[1].id, {"verdict": "fail"}, LabelSource.ACCEPTED)
    assert store.labeled_count(ds.id) == (2, 3)


def test_labeled_count_empty_dataset(store: Store) -> None:
    ds = store.create_dataset("d", "", ["question"], LABEL_SCHEMA)
    assert store.labeled_count(ds.id) == (0, 0)


def test_set_label_missing_row_raises(store: Store) -> None:
    with pytest.raises(NotFoundError):
        store.set_label("nope", {"verdict": "pass"}, LabelSource.MANUAL)


# -- Runs --------------------------------------------------------------------


def _make_run_prereqs(store: Store) -> tuple[str, str]:
    """Create an evaluator+version and a dataset, returning (version_id, dataset_id)."""
    evaluator = store.create_evaluator("e")
    version = store.create_version(evaluator.id, **version_fields())
    ds = store.create_dataset("d", "", ["question"], LABEL_SCHEMA)
    return version.id, ds.id


def test_create_run_freezes_version(store: Store) -> None:
    version_id, dataset_id = _make_run_prereqs(store)
    assert store.get_version(version_id).frozen is False

    run = store.create_run(RunKind.EVAL, version_id, dataset_id, concurrency=4)
    assert run.status is RunStatus.PENDING
    assert run.concurrency == 4
    assert run.kind is RunKind.EVAL
    assert store.get_version(version_id).frozen is True


def test_create_run_missing_version_raises(store: Store) -> None:
    _, dataset_id = _make_run_prereqs(store)
    with pytest.raises(NotFoundError):
        store.create_run(RunKind.EVAL, "nope", dataset_id, concurrency=1)


def test_create_run_missing_dataset_raises(store: Store) -> None:
    version_id, _ = _make_run_prereqs(store)
    with pytest.raises(NotFoundError):
        store.create_run(RunKind.EVAL, version_id, "nope", concurrency=1)
    # The version must not have been frozen by the failed transaction.
    assert store.get_version(version_id).frozen is False


def test_run_status_and_cancel(store: Store) -> None:
    version_id, dataset_id = _make_run_prereqs(store)
    run = store.create_run(RunKind.VALIDATION, version_id, dataset_id, concurrency=2)

    updated = store.update_run_status(run.id, RunStatus.RUNNING)
    assert updated.status is RunStatus.RUNNING

    finished = store.update_run_status(
        run.id, RunStatus.COMPLETED, metrics={"agreement": 0.9}, error=None
    )
    assert finished.status is RunStatus.COMPLETED
    assert finished.metrics == {"agreement": 0.9}

    cancelled = store.request_cancel(run.id)
    assert cancelled.cancel_requested is True
    assert store.get_run(run.id).cancel_requested is True


def test_update_run_status_missing_raises(store: Store) -> None:
    with pytest.raises(NotFoundError):
        store.update_run_status("nope", RunStatus.RUNNING)


def test_get_run_missing_raises(store: Store) -> None:
    with pytest.raises(NotFoundError):
        store.get_run("nope")


def test_list_runs_filters(store: Store) -> None:
    version_id, dataset_id = _make_run_prereqs(store)
    other_version, other_dataset = _make_run_prereqs(store)

    r1 = store.create_run(RunKind.EVAL, version_id, dataset_id, concurrency=1)
    r2 = store.create_run(RunKind.EVAL, other_version, other_dataset, concurrency=1)

    assert {r.id for r in store.list_runs()} == {r1.id, r2.id}
    assert [r.id for r in store.list_runs(version_id=version_id)] == [r1.id]
    assert [r.id for r in store.list_runs(dataset_id=other_dataset)] == [r2.id]
    assert store.list_runs(version_id=version_id, dataset_id=other_dataset) == []


def test_results_crud_and_failed_row_ids(store: Store) -> None:
    version_id, dataset_id = _make_run_prereqs(store)
    run = store.create_run(RunKind.EVAL, version_id, dataset_id, concurrency=1)

    ok = store.add_result(
        run.id, row_id="row-ok", output={"verdict": "pass"}, score_value="pass", error=None
    )
    assert isinstance(ok, RunResult)
    store.add_result(run.id, row_id="row-bad", error="boom")

    results = store.list_results(run.id)
    assert {r.row_id for r in results} == {"row-ok", "row-bad"}

    assert store.failed_result_row_ids(run.id) == ["row-bad"]


def test_add_result_missing_run_raises(store: Store) -> None:
    with pytest.raises(NotFoundError):
        store.add_result("nope", row_id="r")


# -- Cascade deletes ---------------------------------------------------------


def test_delete_evaluator_cascades_versions(store: Store) -> None:
    evaluator = store.create_evaluator("e")
    v1 = store.create_version(evaluator.id, **version_fields(version_name="v1"))
    v2 = store.create_version(evaluator.id, **version_fields(version_name="v2"))

    store.delete_evaluator(evaluator.id)

    assert store.list_versions(evaluator.id) == []
    for vid in (v1.id, v2.id):
        with pytest.raises(NotFoundError):
            store.get_version(vid)
    # No orphan version rows anywhere.
    with session_scope(store.engine) as session:
        from sqlmodel import select

        assert session.exec(select(EvaluatorVersion)).all() == []


def test_delete_dataset_cascades_rows(store: Store) -> None:
    ds = store.create_dataset("d", "", ["question"], LABEL_SCHEMA)
    store.add_rows(ds.id, [{"question": "a"}, {"question": "b"}])

    store.delete_dataset(ds.id)

    assert store.list_rows(ds.id) == []
    with session_scope(store.engine) as session:
        from sqlmodel import select

        assert session.exec(select(DatasetRow)).all() == []


# -- Cross-thread access (WAL / check_same_thread=False) ---------------------


def test_engine_is_usable_across_threads(store: Store) -> None:
    evaluator = store.create_evaluator("threaded")
    results: list[str] = []
    errors: list[Exception] = []

    def worker() -> None:
        try:
            results.append(store.get_evaluator(evaluator.id).name)
        except Exception as exc:  # noqa: BLE001 - recorded for the assertion below
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert errors == []
    assert results == ["threaded"]
