"""Shared workflow transitions for the CLI and API; neither transport owns the contract."""

from datetime import UTC, datetime

from valcore import config
from valcore.errors import ContractError
from valcore.models import (
    DatasetDerivation,
    DerivationRole,
    DerivationState,
    Run,
    RunKind,
    RunStatus,
    check_agent_dataset_compatibility,
    check_dataset_compatibility,
)
from valcore.settings import is_local_cli_model
from valcore.store import Store


def create_pipeline(
    store: Store,
    agent_version_id: str,
    dataset_id: str,
    evaluator_version_ids: list[str],
    concurrency: int,
) -> Run:
    """Preflight the entire contract before atomically freezing and queuing its runs."""
    if not evaluator_version_ids:
        raise ContractError("Select at least one evaluator.")
    if len(set(evaluator_version_ids)) != len(evaluator_version_ids):
        raise ContractError("Select each evaluator version only once.")
    if not 1 <= concurrency <= 64:
        raise ContractError("Concurrency must be between 1 and 64.")
    agent = store.get_agent_version(agent_version_id)
    dataset = store.get_dataset(dataset_id)
    check_agent_dataset_compatibility(agent, dataset)
    contract = DatasetDerivation(
        dataset_id=dataset.id,
        agent_version_id=agent.id,
        response_columns=agent.response_columns,
    )
    if not is_local_cli_model(agent.model):
        config.require_gateway_key()
    for id in evaluator_version_ids:
        evaluator = store.get_version(id)
        if not is_local_cli_model(evaluator.model):
            config.require_gateway_key()
        check_dataset_compatibility(evaluator, dataset, [], kind=RunKind.EVAL, derivation=contract)
    return store.create_agent_pipeline(agent.id, dataset.id, evaluator_version_ids, concurrency)


def prepare_evaluators(store: Store, parent_id: str) -> list[Run]:
    """Attach completed responses, or terminate queued runs when their input cannot be produced.

    The caller owns execution and event delivery. This transition is shared so command-line
    and web workflows make identical decisions about failed rows and upstream failures.
    """
    parent = store.get_run(parent_id)
    _, child_ids = store.run_dependencies(parent_id)
    try:
        if parent.status not in (RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_ERRORS):
            raise ContractError(f"Agent run {parent.status.value}; evaluation was not started.")
        link = store.get_run_derivation(parent_id)
        if link is None:
            raise ContractError("Agent run has no response derivation.")
        if store.derivation_state(link.derivation_id) is DerivationState.STAGED:
            store.save_staged_derivation(link.derivation_id)
        for child_id in child_ids:
            child = store.get_run(child_id)
            if child.cancel_requested or child.status is not RunStatus.PENDING:
                continue
            store.link_run_derivation(child_id, link.derivation_id, DerivationRole.READS)
    except Exception as exc:  # noqa: BLE001 — no queued evaluation may be stranded
        for child_id in child_ids:
            child = store.get_run(child_id)
            if child.status is not RunStatus.PENDING:
                continue
            status = (
                RunStatus.CANCELLED if parent.status is RunStatus.CANCELLED else RunStatus.FAILED
            )
            store.update_run_status(child_id, status, error=str(exc), finished_at=datetime.now(UTC))
    return [store.get_run(id) for id in child_ids]
