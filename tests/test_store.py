"""Tests for the SQLite store layer against a real tmp_path database file."""

import threading
from pathlib import Path

import pytest

from valcore.errors import (
    ContractError,
    DestructiveChangeError,
    FrozenVersionError,
    NotFoundError,
    ReferencedError,
)
from valcore.models import (
    DatasetRow,
    EvaluatorVersion,
    LabelSource,
    Run,
    RunKind,
    RunResult,
    RunStatus,
    ScoreKind,
)
from valcore.store import Store, create_engine, init_db, session_scope


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


def test_create_engine_uses_settings_db_path_when_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VALCORE_HOME", str(tmp_path / "home"))
    from valcore import settings

    settings.get_settings.cache_clear()
    engine = create_engine(None)
    assert "valcore.db" in str(engine.url)


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
    from valcore.errors import ConfigError

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


# -- Manual authoring: update_evaluator --------------------------------------


def test_update_evaluator_changes_name_and_description(store: Store) -> None:
    evaluator = store.create_evaluator("old", "old desc")
    updated = store.update_evaluator(evaluator.id, name="new", description="new desc")
    assert updated.name == "new"
    assert updated.description == "new desc"
    reloaded = store.get_evaluator(evaluator.id)
    assert reloaded.name == "new"
    assert reloaded.description == "new desc"


def test_update_evaluator_missing_raises(store: Store) -> None:
    with pytest.raises(NotFoundError):
        store.update_evaluator("nope", name="x")


# -- Manual authoring: delete_version ----------------------------------------


def _run_referencing_version(store: Store, version_id: str, dataset_id: str) -> Run:
    """Create a run against ``version_id``/``dataset_id`` and return it."""
    return store.create_run(RunKind.EVAL, version_id, dataset_id, concurrency=1)


def test_delete_version_non_active_leaves_pointer(store: Store) -> None:
    evaluator = store.create_evaluator("e")
    v1 = store.create_version(evaluator.id, **version_fields(version_name="v1"))
    v2 = store.create_version(evaluator.id, **version_fields(version_name="v2"))
    # v2 is active; deleting the non-active v1 must not move the pointer.
    store.delete_version(v1.id)
    assert store.get_evaluator(evaluator.id).active_version_id == v2.id
    with pytest.raises(NotFoundError):
        store.get_version(v1.id)


def test_delete_version_active_repoints_to_newest_survivor(store: Store) -> None:
    evaluator = store.create_evaluator("e")
    store.create_version(evaluator.id, **version_fields(version_name="v1"))
    v2 = store.create_version(evaluator.id, **version_fields(version_name="v2"))
    v3 = store.create_version(evaluator.id, **version_fields(version_name="v3"))
    assert store.get_evaluator(evaluator.id).active_version_id == v3.id
    store.delete_version(v3.id)
    assert store.get_evaluator(evaluator.id).active_version_id == v2.id


def test_delete_version_only_version_clears_pointer(store: Store) -> None:
    evaluator = store.create_evaluator("e")
    v1 = store.create_version(evaluator.id, **version_fields())
    assert store.get_evaluator(evaluator.id).active_version_id == v1.id
    store.delete_version(v1.id)
    assert store.get_evaluator(evaluator.id).active_version_id is None
    assert store.list_versions(evaluator.id) == []


def test_delete_version_frozen_succeeds(store: Store) -> None:
    evaluator = store.create_evaluator("e")
    v1 = store.create_version(evaluator.id, **version_fields())
    store.freeze_version(v1.id)
    store.delete_version(v1.id)
    with pytest.raises(NotFoundError):
        store.get_version(v1.id)


def test_delete_version_missing_raises(store: Store) -> None:
    with pytest.raises(NotFoundError):
        store.delete_version("nope")


def test_delete_version_referenced_by_run_raises(store: Store) -> None:
    evaluator = store.create_evaluator("e")
    version = store.create_version(evaluator.id, **version_fields())
    ds = store.create_dataset("d", "", ["question"], LABEL_SCHEMA)
    run = _run_referencing_version(store, version.id, ds.id)

    with pytest.raises(ReferencedError) as exc:
        store.delete_version(version.id)
    assert exc.value.detail["run_count"] == 1
    assert run.id in exc.value.detail["run_ids"]
    # The version must still exist after the blocked delete.
    assert store.get_version(version.id).id == version.id


# -- Manual authoring: delete_evaluator / delete_dataset reference checks -----


def test_delete_evaluator_referenced_by_run_raises(store: Store) -> None:
    evaluator = store.create_evaluator("e")
    version = store.create_version(evaluator.id, **version_fields())
    ds = store.create_dataset("d", "", ["question"], LABEL_SCHEMA)
    run = _run_referencing_version(store, version.id, ds.id)

    with pytest.raises(ReferencedError) as exc:
        store.delete_evaluator(evaluator.id)
    assert exc.value.detail["run_count"] == 1
    assert run.id in exc.value.detail["run_ids"]
    # Blocked, not cascaded: the evaluator and its version survive.
    assert store.get_evaluator(evaluator.id).id == evaluator.id
    assert store.get_version(version.id).id == version.id


def test_delete_dataset_referenced_by_run_raises(store: Store) -> None:
    evaluator = store.create_evaluator("e")
    version = store.create_version(evaluator.id, **version_fields())
    ds = store.create_dataset("d", "", ["question"], LABEL_SCHEMA)
    run = _run_referencing_version(store, version.id, ds.id)

    with pytest.raises(ReferencedError) as exc:
        store.delete_dataset(ds.id)
    assert exc.value.detail["run_count"] == 1
    assert run.id in exc.value.detail["run_ids"]
    assert store.get_dataset(ds.id).id == ds.id


# -- Manual authoring: runs_for_versions / runs_for_dataset ------------------


def test_runs_for_versions_returns_matching_runs(store: Store) -> None:
    evaluator = store.create_evaluator("e")
    version = store.create_version(evaluator.id, **version_fields())
    ds = store.create_dataset("d", "", ["question"], LABEL_SCHEMA)
    run = _run_referencing_version(store, version.id, ds.id)

    assert [r.id for r in store.runs_for_versions([version.id])] == [run.id]
    assert store.runs_for_versions(["other"]) == []
    assert store.runs_for_versions([]) == []


def test_runs_for_dataset_returns_matching_runs(store: Store) -> None:
    evaluator = store.create_evaluator("e")
    version = store.create_version(evaluator.id, **version_fields())
    ds = store.create_dataset("d", "", ["question"], LABEL_SCHEMA)
    run = _run_referencing_version(store, version.id, ds.id)

    assert [r.id for r in store.runs_for_dataset(ds.id)] == [run.id]
    assert store.runs_for_dataset("other") == []


# -- Manual authoring: update_dataset shape migration ------------------------


def _dataset_with_rows(store: Store) -> tuple[str, list[DatasetRow]]:
    """A two-column dataset with three rows carrying data for both columns."""
    ds = store.create_dataset("d", "", ["question", "answer"], LABEL_SCHEMA)
    rows = store.add_rows(
        ds.id,
        [
            {"question": "q1", "answer": "a1"},
            {"question": "q2", "answer": "a2"},
            {"question": "q3", "answer": "a3"},
        ],
    )
    return ds.id, rows


def test_update_dataset_all_none_is_noop(store: Store) -> None:
    ds_id, _ = _dataset_with_rows(store)
    result = store.update_dataset(ds_id)
    assert result.columns == ["question", "answer"]
    assert result.label_schema == LABEL_SCHEMA
    rows = store.list_rows(ds_id)
    assert [set(r.data) for r in rows] == [{"question", "answer"}] * 3


def test_update_dataset_name_and_description(store: Store) -> None:
    ds_id, _ = _dataset_with_rows(store)
    result = store.update_dataset(ds_id, name="renamed", description="new")
    assert result.name == "renamed"
    assert result.description == "new"
    reloaded = store.get_dataset(ds_id)
    assert reloaded.name == "renamed"
    assert reloaded.description == "new"


def test_update_dataset_rename_column_rewrites_row_data(store: Store) -> None:
    ds_id, _ = _dataset_with_rows(store)
    result = store.update_dataset(ds_id, column_renames={"question": "prompt"})
    assert result.columns == ["prompt", "answer"]
    rows = store.list_rows(ds_id)
    for row in rows:
        assert "question" not in row.data
        assert set(row.data) == {"prompt", "answer"}
    assert [r.data["prompt"] for r in rows] == ["q1", "q2", "q3"]


def test_update_dataset_add_column_backfills_none(store: Store) -> None:
    ds_id, _ = _dataset_with_rows(store)
    result = store.update_dataset(ds_id, columns=["question", "answer", "context"])
    assert result.columns == ["question", "answer", "context"]
    for row in store.list_rows(ds_id):
        assert row.data["context"] is None


def test_update_dataset_remove_column_drops_key(store: Store) -> None:
    ds_id, _ = _dataset_with_rows(store)
    result = store.update_dataset(ds_id, columns=["question"])
    assert result.columns == ["question"]
    for row in store.list_rows(ds_id):
        assert set(row.data) == {"question"}


def test_update_dataset_unknown_rename_key_raises(store: Store) -> None:
    ds_id, _ = _dataset_with_rows(store)
    with pytest.raises(ContractError):
        store.update_dataset(ds_id, column_renames={"nonexistent": "x"})
    # Nothing changed.
    assert store.get_dataset(ds_id).columns == ["question", "answer"]


def test_update_dataset_rename_untouched_by_schema_keeps_labels(store: Store) -> None:
    ds = store.create_dataset("d", "", ["question", "answer"], LABEL_SCHEMA)
    rows = store.add_rows(ds.id, [{"question": "q1", "answer": "a1"}])
    store.set_label(rows[0].id, {"value": "pass"}, LabelSource.MANUAL)

    store.update_dataset(ds.id, column_renames={"question": "prompt"})

    reloaded = store.list_rows(ds.id)[0]
    assert reloaded.label == {"value": "pass"}
    assert reloaded.label_source is LabelSource.MANUAL


def test_update_dataset_zero_rows_shape_change_needs_no_force(store: Store) -> None:
    schema = {"kind": "categorical", "labels": ["pass", "fail", "maybe"]}
    ds = store.create_dataset("d", "", ["question"], schema)
    narrowed = {"kind": "categorical", "labels": ["pass", "fail"]}
    result = store.update_dataset(ds.id, columns=["question", "answer"], label_schema=narrowed)
    assert result.columns == ["question", "answer"]
    assert result.label_schema == narrowed


def _narrowing_dataset(store: Store) -> tuple[str, list[DatasetRow]]:
    """A categorical dataset whose third row holds a label about to become invalid."""
    schema = {"kind": "categorical", "labels": ["pass", "fail", "maybe"]}
    ds = store.create_dataset("d", "", ["question"], schema)
    rows = store.add_rows(ds.id, [{"question": "q1"}, {"question": "q2"}, {"question": "q3"}])
    store.set_label(rows[0].id, {"value": "pass"}, LabelSource.MANUAL)
    store.set_label(rows[1].id, {"value": "fail"}, LabelSource.ACCEPTED)
    store.set_label(rows[2].id, {"value": "maybe"}, LabelSource.MANUAL)
    return ds.id, rows


def test_update_dataset_narrowing_without_force_raises_and_rolls_back(store: Store) -> None:
    ds_id, _rows = _narrowing_dataset(store)
    narrowed = {"kind": "categorical", "labels": ["pass", "fail"]}

    with pytest.raises(DestructiveChangeError) as exc:
        store.update_dataset(ds_id, columns=["prompt"], label_schema=narrowed)
    assert exc.value.detail["invalid_label_count"] == 1

    # Full rollback: neither the schema/columns nor any label changed.
    reloaded = store.get_dataset(ds_id)
    assert reloaded.columns == ["question"]
    assert reloaded.label_schema == {"kind": "categorical", "labels": ["pass", "fail", "maybe"]}
    persisted = store.list_rows(ds_id)
    assert [r.label for r in persisted] == [
        {"value": "pass"},
        {"value": "fail"},
        {"value": "maybe"},
    ]


def test_update_dataset_narrowing_with_force_clears_only_invalid_labels(store: Store) -> None:
    ds_id, rows = _narrowing_dataset(store)
    narrowed = {"kind": "categorical", "labels": ["pass", "fail"]}

    result = store.update_dataset(ds_id, label_schema=narrowed, force=True)
    assert result.label_schema == narrowed

    persisted = {r.id: r for r in store.list_rows(ds_id)}
    # The valid labels are untouched, including their source.
    assert persisted[rows[0].id].label == {"value": "pass"}
    assert persisted[rows[0].id].label_source is LabelSource.MANUAL
    assert persisted[rows[1].id].label == {"value": "fail"}
    assert persisted[rows[1].id].label_source is LabelSource.ACCEPTED
    # Exactly the invalid row is cleared.
    assert persisted[rows[2].id].label is None
    assert persisted[rows[2].id].label_source is None


def test_update_dataset_missing_raises(store: Store) -> None:
    with pytest.raises(NotFoundError):
        store.update_dataset("nope", name="x")


# -- Manual authoring: delete_row --------------------------------------------


def test_delete_row_removes_one_leaves_rest(store: Store) -> None:
    ds_id, rows = _dataset_with_rows(store)
    store.delete_row(rows[1].id)
    remaining = store.list_rows(ds_id)
    assert [r.id for r in remaining] == [rows[0].id, rows[2].id]
    with pytest.raises(NotFoundError):
        store.get_row(rows[1].id)


def test_delete_row_missing_raises(store: Store) -> None:
    with pytest.raises(NotFoundError):
        store.delete_row("nope")
