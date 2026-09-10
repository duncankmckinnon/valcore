"""Tests for the SQLite store layer against a real tmp_path database file."""

import threading
from pathlib import Path

import pytest
from sqlmodel import select

from valcore.errors import (
    ContractError,
    FrozenVersionError,
    NotFoundError,
    ReferencedError,
)
from valcore.models import (
    DatasetGeneration,
    DatasetLogfirePull,
    DatasetRow,
    EvaluatorVersion,
    ExperimentRun,
    LabelSet,
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
    ds = store.create_dataset("d", "desc", ["question", "answer"])
    assert ds.columns == ["question", "answer"]

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
    ds = store.create_dataset("d", "", ["question"])
    first = store.add_rows(ds.id, [{"question": "a"}, {"question": "b"}])
    assert [r.idx for r in first] == [0, 1]

    second = store.add_rows(ds.id, [{"question": "c"}, {"question": "d"}, {"question": "e"}])
    assert [r.idx for r in second] == [2, 3, 4]

    rows = store.list_rows(ds.id)
    assert [r.idx for r in rows] == [0, 1, 2, 3, 4]
    assert [r.data["question"] for r in rows] == ["a", "b", "c", "d", "e"]


def test_add_rows_empty_first_call_starts_at_zero(store: Store) -> None:
    ds = store.create_dataset("d", "", ["question"])
    assert store.add_rows(ds.id, []) == []
    rows = store.add_rows(ds.id, [{"question": "a"}])
    assert [r.idx for r in rows] == [0]


def test_add_rows_missing_dataset_raises(store: Store) -> None:
    with pytest.raises(NotFoundError):
        store.add_rows("nope", [{"question": "a"}])


# -- Label sets ----------------------------------------------------------


def test_create_and_get_label_set(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    created = store.create_label_set(
        dataset.id,
        name="quality",
        description="human review",
        kind=ScoreKind.CATEGORICAL,
        labels=[{"name": "good", "description": "meets the bar"}],
    )
    assert created.id
    assert created.dataset_id == dataset.id
    assert created.kind is ScoreKind.CATEGORICAL

    fetched = store.get_label_set(created.id)
    assert fetched.name == "quality"
    assert fetched.labels == [{"name": "good", "description": "meets the bar"}]


def test_create_label_set_rejects_invalid_shape(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    with pytest.raises(ContractError):
        store.create_label_set(
            dataset.id, name="bad", description="", kind=ScoreKind.CATEGORICAL, labels=None
        )


def test_list_label_sets_ordered_by_creation(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    first = store.create_label_set(
        dataset.id,
        name="a",
        description="",
        kind=ScoreKind.CATEGORICAL,
        labels=[{"name": "x", "description": "d"}],
    )
    second = store.create_label_set(
        dataset.id,
        name="b",
        description="",
        kind=ScoreKind.NUMERIC,
        minimum=0.0,
        maximum=1.0,
    )
    listed = store.list_label_sets(dataset.id)
    assert [ls.id for ls in listed] == [first.id, second.id]


def test_update_label_set_renames(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    created = store.create_label_set(
        dataset.id,
        name="a",
        description="",
        kind=ScoreKind.CATEGORICAL,
        labels=[{"name": "x", "description": "d"}],
    )
    updated = store.update_label_set(created.id, name="renamed", description="new desc")
    assert updated.name == "renamed"
    assert updated.description == "new desc"


def test_delete_label_set_removes_it(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    label_set = store.create_label_set(
        dataset.id,
        name="a",
        description="",
        kind=ScoreKind.CATEGORICAL,
        labels=[{"name": "x", "description": "d"}],
    )
    store.delete_label_set(label_set.id)
    with pytest.raises(NotFoundError):
        store.get_label_set(label_set.id)


def test_primary_label_set_returns_oldest(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    first = store.create_label_set(
        dataset.id,
        name="a",
        description="",
        kind=ScoreKind.CATEGORICAL,
        labels=[{"name": "x", "description": "d"}],
    )
    store.create_label_set(
        dataset.id,
        name="b",
        description="",
        kind=ScoreKind.NUMERIC,
        minimum=0.0,
        maximum=1.0,
    )
    assert store.primary_label_set(dataset.id).id == first.id


def test_primary_label_set_none_when_dataset_has_none(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    assert store.primary_label_set(dataset.id) is None


# -- Annotations ---------------------------------------------------------


def _categorical_label_set(store: Store, dataset_id: str) -> LabelSet:
    return store.create_label_set(
        dataset_id,
        name="quality",
        description="",
        kind=ScoreKind.CATEGORICAL,
        labels=[{"name": "good", "description": "d"}, {"name": "bad", "description": "d"}],
    )


def test_set_annotation_creates_then_updates_in_place(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    rows = store.add_rows(dataset.id, [{"q": "1"}])
    label_set = _categorical_label_set(store, dataset.id)

    created = store.set_annotation(
        label_set.id,
        rows[0].id,
        labels=["good"],
        description="looks right",
        source=LabelSource.MANUAL,
    )
    assert created.labels == ["good"]
    assert created.description == "looks right"
    assert created.source is LabelSource.MANUAL

    updated = store.set_annotation(
        label_set.id, rows[0].id, labels=["good", "bad"], description="revised"
    )
    assert updated.id == created.id
    assert updated.labels == ["good", "bad"]
    assert updated.description == "revised"

    all_annotations = store.list_annotations_for_rows(label_set.id, [rows[0].id])
    assert len(all_annotations) == 1


def test_set_annotation_rejects_invalid_labels(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    rows = store.add_rows(dataset.id, [{"q": "1"}])
    label_set = _categorical_label_set(store, dataset.id)
    with pytest.raises(ContractError):
        store.set_annotation(label_set.id, rows[0].id, labels=["unknown"])


def test_get_annotation_returns_none_when_absent(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    rows = store.add_rows(dataset.id, [{"q": "1"}])
    label_set = _categorical_label_set(store, dataset.id)
    assert store.get_annotation(label_set.id, rows[0].id) is None


def test_clear_annotation_removes_it(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    rows = store.add_rows(dataset.id, [{"q": "1"}])
    label_set = _categorical_label_set(store, dataset.id)
    store.set_annotation(label_set.id, rows[0].id, labels=["good"])
    store.clear_annotation(label_set.id, rows[0].id)
    assert store.get_annotation(label_set.id, rows[0].id) is None


def test_set_annotation_writes_suggested_fields_without_confirming(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    rows = store.add_rows(dataset.id, [{"q": "1"}])
    label_set = _categorical_label_set(store, dataset.id)

    annotation = store.set_annotation(
        label_set.id,
        rows[0].id,
        suggested_labels=["good"],
        reasoning="looks right",
        source=LabelSource.GENERATED,
    )
    assert annotation.suggested_labels == ["good"]
    assert annotation.reasoning == "looks right"
    assert annotation.source is LabelSource.GENERATED
    # Confirmed fields are untouched by a suggestion-only call.
    assert annotation.labels == []
    assert annotation.value is None


def test_set_annotation_suggested_value_for_numeric(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    rows = store.add_rows(dataset.id, [{"q": "1"}])
    label_set = store.create_label_set(
        dataset.id,
        name="score",
        description="",
        kind=ScoreKind.NUMERIC,
        minimum=0.0,
        maximum=1.0,
    )
    annotation = store.set_annotation(label_set.id, rows[0].id, suggested_value=0.7)
    assert annotation.suggested_value == 0.7
    assert annotation.value is None


def test_set_annotation_confirming_a_suggestion_is_a_separate_call(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    rows = store.add_rows(dataset.id, [{"q": "1"}])
    label_set = _categorical_label_set(store, dataset.id)

    store.set_annotation(
        label_set.id, rows[0].id, suggested_labels=["good"], source=LabelSource.GENERATED
    )
    confirmed = store.set_annotation(
        label_set.id, rows[0].id, labels=["good"], source=LabelSource.ACCEPTED
    )
    assert confirmed.labels == ["good"]
    assert confirmed.suggested_labels == ["good"]  # untouched by the confirming call
    assert confirmed.source is LabelSource.ACCEPTED


def test_list_annotations_for_rows_filters_by_row_ids(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    rows = store.add_rows(dataset.id, [{"q": "1"}, {"q": "2"}])
    label_set = _categorical_label_set(store, dataset.id)
    store.set_annotation(label_set.id, rows[0].id, labels=["good"])
    store.set_annotation(label_set.id, rows[1].id, labels=["bad"])
    only_first = store.list_annotations_for_rows(label_set.id, [rows[0].id])
    assert [a.dataset_row_id for a in only_first] == [rows[0].id]


def test_list_annotations_for_rows_empty_ids_returns_empty(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    label_set = _categorical_label_set(store, dataset.id)
    assert store.list_annotations_for_rows(label_set.id, []) == []


def test_accept_annotation_suggestion_categorical(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    rows = store.add_rows(dataset.id, [{"q": "1"}])
    label_set = _categorical_label_set(store, dataset.id)
    store.set_annotation(
        label_set.id, rows[0].id, suggested_labels=["good"], source=LabelSource.GENERATED
    )

    accepted = store.accept_annotation_suggestion(label_set.id, rows[0].id)
    assert accepted.labels == ["good"]
    assert accepted.source is LabelSource.ACCEPTED
    assert accepted.suggested_labels == ["good"]  # the suggestion itself is preserved, not cleared


def test_accept_annotation_suggestion_numeric(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    rows = store.add_rows(dataset.id, [{"q": "1"}])
    label_set = store.create_label_set(
        dataset.id,
        name="score",
        description="",
        kind=ScoreKind.NUMERIC,
        minimum=0.0,
        maximum=1.0,
    )
    store.set_annotation(
        label_set.id, rows[0].id, suggested_value=0.7, source=LabelSource.GENERATED
    )

    accepted = store.accept_annotation_suggestion(label_set.id, rows[0].id)
    assert accepted.value == 0.7
    assert accepted.source is LabelSource.ACCEPTED


def test_accept_annotation_suggestion_raises_when_no_annotation(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    rows = store.add_rows(dataset.id, [{"q": "1"}])
    label_set = _categorical_label_set(store, dataset.id)
    with pytest.raises(ContractError, match="no suggested"):
        store.accept_annotation_suggestion(label_set.id, rows[0].id)


def test_accept_annotation_suggestion_raises_when_annotation_has_no_suggestion(
    store: Store,
) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    rows = store.add_rows(dataset.id, [{"q": "1"}])
    label_set = _categorical_label_set(store, dataset.id)
    store.set_annotation(label_set.id, rows[0].id, labels=["good"], source=LabelSource.MANUAL)
    with pytest.raises(ContractError, match="no suggested"):
        store.accept_annotation_suggestion(label_set.id, rows[0].id)


def test_annotation_progress_counts_annotated_and_total(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    rows = store.add_rows(dataset.id, [{"q": "1"}, {"q": "2"}, {"q": "3"}])
    label_set = _categorical_label_set(store, dataset.id)
    store.set_annotation(label_set.id, rows[0].id, labels=["good"])
    annotated, total = store.annotation_progress(label_set.id)
    assert (annotated, total) == (1, 3)


def test_delete_label_set_cascades_annotations(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    rows = store.add_rows(dataset.id, [{"q": "1"}])
    label_set = _categorical_label_set(store, dataset.id)
    store.set_annotation(label_set.id, rows[0].id, labels=["good"])
    store.delete_label_set(label_set.id)
    with pytest.raises(NotFoundError):
        store.get_label_set(label_set.id)
    assert store.get_annotation(label_set.id, rows[0].id) is None


# -- Runs --------------------------------------------------------------------


def _make_run_prereqs(store: Store) -> tuple[str, str]:
    """Create an evaluator+version and a dataset, returning (version_id, dataset_id)."""
    evaluator = store.create_evaluator("e")
    version = store.create_version(evaluator.id, **version_fields())
    ds = store.create_dataset("d", "", ["question"])
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
    ds = store.create_dataset("d", "", ["question"])
    store.add_rows(ds.id, [{"question": "a"}, {"question": "b"}])

    store.delete_dataset(ds.id)

    assert store.list_rows(ds.id) == []
    with session_scope(store.engine) as session:
        from sqlmodel import select

        assert session.exec(select(DatasetRow)).all() == []


def test_delete_row_cascades_annotations(store: Store) -> None:
    dataset = store.create_dataset("ds", "", ["q"])
    rows = store.add_rows(dataset.id, [{"q": "1"}, {"q": "2"}])
    label_set = _categorical_label_set(store, dataset.id)
    store.set_annotation(label_set.id, rows[0].id, labels=["good"])
    store.set_annotation(label_set.id, rows[1].id, labels=["bad"])

    store.delete_row(rows[0].id)

    assert store.get_annotation(label_set.id, rows[0].id) is None
    annotated, total = store.annotation_progress(label_set.id)
    assert (annotated, total) == (1, 1)
    # Verify the other row's annotation still exists
    assert store.get_annotation(label_set.id, rows[1].id) is not None


def test_delete_dataset_cascades_label_sets_and_annotations(store: Store) -> None:
    ds = store.create_dataset("d", "", ["question"])
    rows = store.add_rows(ds.id, [{"question": "a"}])
    label_set = _categorical_label_set(store, ds.id)
    store.set_annotation(label_set.id, rows[0].id, labels=["good"])

    store.delete_dataset(ds.id)

    with pytest.raises(NotFoundError):
        store.get_label_set(label_set.id)
    assert store.get_annotation(label_set.id, rows[0].id) is None


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
    ds = store.create_dataset("d", "", ["question"])
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
    ds = store.create_dataset("d", "", ["question"])
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
    ds = store.create_dataset("d", "", ["question"])
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
    ds = store.create_dataset("d", "", ["question"])
    run = _run_referencing_version(store, version.id, ds.id)

    assert [r.id for r in store.runs_for_versions([version.id])] == [run.id]
    assert store.runs_for_versions(["other"]) == []
    assert store.runs_for_versions([]) == []


def test_runs_for_dataset_returns_matching_runs(store: Store) -> None:
    evaluator = store.create_evaluator("e")
    version = store.create_version(evaluator.id, **version_fields())
    ds = store.create_dataset("d", "", ["question"])
    run = _run_referencing_version(store, version.id, ds.id)

    assert [r.id for r in store.runs_for_dataset(ds.id)] == [run.id]
    assert store.runs_for_dataset("other") == []


# -- Manual authoring: update_dataset shape migration ------------------------


def _dataset_with_rows(store: Store) -> tuple[str, list[DatasetRow]]:
    """A two-column dataset with three rows carrying data for both columns."""
    ds = store.create_dataset("d", "", ["question", "answer"])
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


# -- Manual authoring: additional interaction coverage -----------------------


def test_update_dataset_columns_and_renames_together(store: Store) -> None:
    """When both are given, renames apply, then rows are pruned to the final columns."""
    ds_id, _ = _dataset_with_rows(store)
    result = store.update_dataset(
        ds_id,
        columns=["prompt", "context"],
        column_renames={"question": "prompt"},
    )
    assert result.columns == ["prompt", "context"]
    rows = store.list_rows(ds_id)
    assert [r.data["prompt"] for r in rows] == ["q1", "q2", "q3"]
    for row in rows:
        assert set(row.data) == {"prompt", "context"}
        assert row.data["context"] is None
        assert "question" not in row.data
        assert "answer" not in row.data


def test_delete_version_referenced_leaves_active_pointer(store: Store) -> None:
    """A blocked version delete must not disturb the active pointer."""
    evaluator = store.create_evaluator("e")
    v1 = store.create_version(evaluator.id, **version_fields(version_name="v1"))
    v2 = store.create_version(evaluator.id, **version_fields(version_name="v2"))
    ds = store.create_dataset("d", "", ["question"])
    _run_referencing_version(store, v2.id, ds.id)

    with pytest.raises(ReferencedError):
        store.delete_version(v2.id)
    assert store.get_version(v1.id).id == v1.id
    assert store.get_evaluator(evaluator.id).active_version_id == v2.id


# -- Generation settings ------------------------------------------------------


def test_generation_absent_for_a_dataset_that_was_never_generated(store: Store) -> None:
    dataset = store.create_dataset(name="uploaded", description="", columns=["a"])

    assert store.get_generation(dataset.id) is None


def test_set_generation_round_trips_every_field(store: Store) -> None:
    dataset = store.create_dataset(name="gen", description="", columns=["a"])

    store.set_generation(
        dataset.id,
        count=7,
        instructions="be subtle",
        column_notes={"a": "a prompt"},
        label_mix={"pass": 0.5, "fail": 0.5},
        label_guidance="partial is fail",
        include_labels=True,
        source_version_id="v1",
    )

    stored = store.get_generation(dataset.id)
    assert stored is not None
    assert stored.count == 7
    assert stored.instructions == "be subtle"
    assert stored.column_notes == {"a": "a prompt"}
    assert stored.label_mix == {"pass": 0.5, "fail": 0.5}
    assert stored.label_guidance == "partial is fail"
    assert stored.include_labels is True
    assert stored.source_version_id == "v1"


def test_set_generation_replaces_rather_than_accumulates(store: Store) -> None:
    # The settings describe the most recent ask, which is what a form repopulates from.
    dataset = store.create_dataset(name="gen", description="", columns=["a"])

    store.set_generation(dataset.id, count=5, instructions="first")
    store.set_generation(dataset.id, count=9, instructions="second")

    stored = store.get_generation(dataset.id)
    assert stored is not None
    assert stored.count == 9
    assert stored.instructions == "second"


def test_get_generation_raises_for_a_missing_dataset(store: Store) -> None:
    with pytest.raises(NotFoundError):
        store.get_generation("nope")


def test_deleting_a_dataset_removes_its_generation_record(store: Store) -> None:
    dataset = store.create_dataset(name="gen", description="", columns=["a"])
    store.set_generation(dataset.id, count=3)

    store.delete_dataset(dataset.id)

    with pytest.raises(NotFoundError):
        store.get_generation(dataset.id)


def test_init_db_adds_the_generation_table_to_an_existing_database(tmp_path) -> None:
    """A database written before ``DatasetGeneration`` existed gains it on the next start.

    This is why the settings live in their own table: ``init_db`` is a bare ``create_all``,
    which creates missing tables but never missing columns. Extra fields on ``Dataset``
    would leave an existing database raising ``no such column`` on every dataset query.
    """
    engine = create_engine(tmp_path / "existing.db")
    init_db(engine)
    store = Store(engine)
    dataset = store.create_dataset(name="from-before", description="d", columns=["a"])
    store.add_rows(dataset.id, [{"a": "1"}])

    # Simulate the older schema: the table simply is not there.
    DatasetGeneration.__table__.drop(engine)

    init_db(engine)

    # Existing data is untouched, and the new table is usable.
    assert store.get_dataset(dataset.id).name == "from-before"
    assert len(store.list_rows(dataset.id)) == 1
    assert store.get_generation(dataset.id) is None
    store.set_generation(dataset.id, count=4, instructions="works")
    assert store.get_generation(dataset.id).count == 4


# -- Experiment runs -----------------------------------------------------------


def test_init_db_adds_the_experiment_run_table_to_an_existing_database(tmp_path) -> None:
    """A database written before ``ExperimentRun`` existed gains it on the next start.

    ``ExperimentRun`` is a separate table rather than a ``Run`` column precisely because
    ``init_db`` is a bare ``create_all``: it adds missing tables but never missing columns,
    so a new table reaches an existing database while a new ``Run`` field would not.
    """
    engine = create_engine(tmp_path / "existing.db")
    init_db(engine)
    store = Store(engine)
    version_id, dataset_id = _make_run_prereqs(store)
    run = store.create_run(RunKind.EVAL, version_id, dataset_id, concurrency=1)

    # Simulate the older schema: the table simply is not there.
    ExperimentRun.__table__.drop(engine)

    init_db(engine)

    # Existing data is untouched, and the new table is usable.
    assert store.get_run(run.id).id == run.id
    assert store.get_experiment(run.id) is None
    store.set_experiment(run.id, experiment_name="exp1", case_count=2)
    assert store.get_experiment(run.id).experiment_name == "exp1"


def test_experiment_absent_for_a_run_with_no_row(store: Store) -> None:
    version_id, dataset_id = _make_run_prereqs(store)
    run = store.create_run(RunKind.EVAL, version_id, dataset_id, concurrency=1)

    assert store.get_experiment(run.id) is None


def test_set_experiment_then_get_experiment_round_trips(store: Store) -> None:
    version_id, dataset_id = _make_run_prereqs(store)
    run = store.create_run(RunKind.EVAL, version_id, dataset_id, concurrency=1)

    store.set_experiment(run.id, experiment_name="nightly-eval", case_count=42)

    stored = store.get_experiment(run.id)
    assert stored is not None
    assert stored.run_id == run.id
    assert stored.experiment_name == "nightly-eval"
    assert stored.case_count == 42


def test_set_experiment_replaces_rather_than_accumulates(store: Store) -> None:
    """A second ``set_experiment`` call replaces the row rather than adding a second one."""
    version_id, dataset_id = _make_run_prereqs(store)
    run = store.create_run(RunKind.EVAL, version_id, dataset_id, concurrency=1)

    store.set_experiment(run.id, experiment_name="exp1", case_count=1)
    store.set_experiment(run.id, experiment_name="exp2", case_count=2)

    stored = store.get_experiment(run.id)
    assert stored.experiment_name == "exp2"
    assert stored.case_count == 2

    with session_scope(store.engine) as session:
        rows = session.exec(select(ExperimentRun).where(ExperimentRun.run_id == run.id)).all()
        assert len(rows) == 1


def test_get_experiment_missing_run_raises(store: Store) -> None:
    with pytest.raises(NotFoundError):
        store.get_experiment("nope")


def test_set_experiment_missing_run_raises(store: Store) -> None:
    with pytest.raises(NotFoundError):
        store.set_experiment("nope", experiment_name="exp1", case_count=1)


def test_request_cancel_works_for_a_run_with_no_experiment_row(store: Store) -> None:
    version_id, dataset_id = _make_run_prereqs(store)
    run = store.create_run(RunKind.EVAL, version_id, dataset_id, concurrency=1)

    cancelled = store.request_cancel(run.id)

    assert cancelled.cancel_requested is True
    assert store.get_run(run.id).cancel_requested is True


def test_request_cancel_raises_for_an_experiment_run(store: Store) -> None:
    version_id, dataset_id = _make_run_prereqs(store)
    run = store.create_run(RunKind.EVAL, version_id, dataset_id, concurrency=1)
    store.set_experiment(run.id, experiment_name="exp1", case_count=5)

    with pytest.raises(ContractError) as excinfo:
        store.request_cancel(run.id)

    message = str(excinfo.value).lower()
    assert "experiment" in message
    assert "cannot be cancelled" in message
    # Cancellation must not have been flagged despite the raised error.
    assert store.get_run(run.id).cancel_requested is False


def test_logfire_pull_round_trips_and_is_removed_with_dataset(store: Store) -> None:
    dataset = store.create_dataset(name="pulled", description="", columns=["span_id"])
    stored = store.set_logfire_pull(
        dataset.id,
        sql="SELECT span_id FROM records",
        sample_n=5,
        seed=7,
        min_timestamp=None,
        max_timestamp=None,
        label_column=None,
    )
    assert stored.sql == "SELECT span_id FROM records"
    assert store.get_logfire_pull(dataset.id).seed == 7

    store.delete_dataset(dataset.id)
    with pytest.raises(NotFoundError):
        store.get_logfire_pull(dataset.id)


def test_init_db_adds_the_logfire_pull_table_to_an_existing_database(tmp_path) -> None:
    engine = create_engine(tmp_path / "existing.db")
    init_db(engine)
    store = Store(engine)
    dataset = store.create_dataset(name="from-before", description="", columns=["a"])
    DatasetLogfirePull.__table__.drop(engine)
    init_db(engine)
    store.set_logfire_pull(dataset.id, sql="SELECT 1", sample_n=1, seed=1)
    assert store.get_logfire_pull(dataset.id).sql == "SELECT 1"
