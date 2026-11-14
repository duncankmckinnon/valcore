"""Workflow contract checks that must stay identical across API and CLI."""

import pytest

from valcore.errors import ConfigError
from valcore.models import RunStatus, ScoreKind
from valcore.pipeline import create_pipeline, prepare_evaluators
from valcore.store import Store, create_engine, init_db


@pytest.fixture
def workflow(tmp_path):
    engine = create_engine(tmp_path / "pipeline.db")
    init_db(engine)
    store = Store(engine)
    agent = store.create_agent("writer")
    agent_version = store.create_agent_version(
        agent.id,
        version_name="v1",
        model="local/codex",
        spec={"instructions": "Respond."},
        prompt_template="{input}",
        required_columns=["input"],
        deps_mapping={},
    )
    evaluator = store.create_evaluator("judge")
    judge = store.create_version(
        evaluator.id,
        version_name="v1",
        model="gateway/openai:gpt-5",
        instructions="Judge.",
        prompt_template="{response}",
        required_columns=["response"],
        output_fields=[{"name": "score", "type": "float", "description": "Score"}],
        score_field="score",
        score_kind=ScoreKind.NUMERIC,
    )
    dataset = store.create_dataset("cases", "", ["input"])
    try:
        yield store, agent_version, judge, dataset
    finally:
        engine.dispose()


def test_hosted_evaluator_requires_key_even_when_agent_is_local(workflow, monkeypatch):
    store, agent, judge, dataset = workflow
    monkeypatch.setattr(
        "valcore.pipeline.config.require_gateway_key",
        lambda: (_ for _ in ()).throw(ConfigError("Missing key")),
    )
    with pytest.raises(ConfigError, match="Missing key"):
        create_pipeline(store, agent.id, dataset.id, [judge.id], 2)
    assert store.list_runs() == []
    assert not store.get_agent_version(agent.id).frozen


def test_response_preparation_failure_finishes_queued_evaluators(workflow, monkeypatch):
    store, agent, judge, dataset = workflow
    parent = store.create_agent_pipeline(agent.id, dataset.id, [judge.id], 2)
    store.update_run_status(parent.id, RunStatus.COMPLETED)

    def fail(_):
        raise RuntimeError("Could not save responses")

    monkeypatch.setattr(store, "save_staged_derivation", fail)
    children = prepare_evaluators(store, parent.id)
    assert len(children) == 1
    assert children[0].status is RunStatus.FAILED
    assert children[0].error == "Could not save responses"
