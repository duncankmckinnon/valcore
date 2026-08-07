"""Resolve human-friendly command-line references to stored entities.

Requiring a 32-character hex uuid on a command line is not a workflow. Each
resolver matches on exact name first, then on a unique id prefix of at least four
characters. Ambiguity raises :class:`ContractError` listing every candidate;
nothing matching raises :class:`NotFoundError` naming what was searched.
"""

from collections.abc import Callable, Sequence
from typing import TypeVar

from valcore.errors import ContractError, NotFoundError
from valcore.models import Evaluator, EvaluatorVersion
from valcore.store import DatasetSummary, Store

_MIN_PREFIX = 4

T = TypeVar("T")


def _candidate_line(entity: T, id_of: Callable[[T], str], name_of: Callable[[T], str]) -> str:
    """Format one ambiguity candidate as ``<id-prefix>  <name>``."""
    return f"{id_of(entity)[:8]}  {name_of(entity)}"


def _resolve(
    entities: Sequence[T],
    ref: str,
    kind: str,
    *,
    id_of: Callable[[T], str],
    name_of: Callable[[T], str],
) -> T:
    """Resolve ``ref`` to a single entity by exact name then unique id prefix."""
    exact = [e for e in entities if name_of(e) == ref]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        candidates = "\n".join(_candidate_line(e, id_of, name_of) for e in exact)
        raise ContractError(f"{ref!r} matches multiple {kind}s by name:\n{candidates}")

    if len(ref) < _MIN_PREFIX:
        raise NotFoundError(
            f"No {kind} named {ref!r}; an id prefix must be at least "
            f"{_MIN_PREFIX} characters to match by id."
        )

    prefixed = [e for e in entities if id_of(e).startswith(ref)]
    if len(prefixed) == 1:
        return prefixed[0]
    if len(prefixed) > 1:
        candidates = "\n".join(_candidate_line(e, id_of, name_of) for e in prefixed)
        raise ContractError(f"{ref!r} is an ambiguous {kind} id prefix:\n{candidates}")

    raise NotFoundError(f"No {kind} matches {ref!r} by name or id prefix.")


def resolve_evaluator(store: Store, ref: str) -> Evaluator:
    """Resolve ``ref`` to an evaluator by exact name or unique id prefix."""
    return _resolve(
        store.list_evaluators(),
        ref,
        "evaluator",
        id_of=lambda e: e.id,
        name_of=lambda e: e.name,
    )


def resolve_dataset(store: Store, ref: str) -> DatasetSummary:
    """Resolve ``ref`` to a dataset by exact name or unique id prefix.

    ``list_datasets`` yields ``DatasetSummary`` (a dataset plus its counts), so that is what
    resolution returns; callers that only read ``.id``/``.name`` are unaffected.
    """
    return _resolve(
        store.list_datasets(),
        ref,
        "dataset",
        id_of=lambda d: d.id,
        name_of=lambda d: d.name,
    )


def resolve_version(store: Store, evaluator: Evaluator, name: str | None) -> EvaluatorVersion:
    """Resolve a version of ``evaluator`` by name, or its active version when ``name`` is ``None``.

    With ``name=None`` the evaluator's active version is returned, erroring clearly
    when it has none. Otherwise the version is resolved by exact ``version_name``
    then unique id prefix among that evaluator's versions.
    """
    if name is None:
        if evaluator.active_version_id is None:
            raise NotFoundError(
                f"Evaluator {evaluator.name!r} has no active version; pass --version to pick one."
            )
        return store.get_version(evaluator.active_version_id)

    return _resolve(
        store.list_versions(evaluator.id),
        name,
        "version",
        id_of=lambda v: v.id,
        name_of=lambda v: v.version_name,
    )
