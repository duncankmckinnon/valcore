"""JSON assembly, serialization, and format detection for eval packages.

This module is the one place a valcore entity becomes an eval-package JSON document and back.
It owns the *packaging* — which sections go in which file, the ``kind``/``version`` envelope, the
``ValcoreJudge`` evaluator reference, and the four-way format detection in ``from_text`` — while
delegating every foreign-model mapping to ``spec.py``. It renders no Python source (``export.py``
owns that), touches no store, and never reads YAML: the package is JSON, and only JSON, so a
browser with no extra dependencies can parse it with ``JSON.parse``.

The two ``Dataset`` names are aliased on import for the same reason ``spec.py`` aliases them —
``valcore.models.Dataset`` as ``VDataset`` and ``pydantic_evals.Dataset`` as ``EvalsDataset`` —
because confusing the storage entity with the serialization format is the likeliest bug here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError
from pydantic_ai.agent.spec import AgentSpec
from pydantic_evals import Dataset as EvalsDataset

from valcore.errors import ContractError
from valcore.models import Dataset as VDataset
from valcore.models import DatasetRow, EvaluatorVersion, ScoreKind
from valcore.spec import (
    dataset_to_evals,
    evals_to_dataset_fields,
    spec_to_version_fields,
    valcore_meta,
    version_to_spec,
)

_KIND = "valcore/eval-package"
_VERSION = 1

# The one evaluator name a valcore package may reference. Import never reconstructs a foreign
# evaluator: an ``evaluators`` entry naming anything else is a contract violation, and this name
# is the class pydantic-evals looks up in its registry when the emitted companion runs.
_JUDGE_NAME = "ValcoreJudge"

# Concrete generics are used everywhere ``EvalsDataset`` is (de)serialized: constructing or
# loading with unparameterized generics emits a ``UserWarning``. This mirrors ``spec.py``.
_EvalsDataset = EvalsDataset[dict, object, dict]


def _dumps(doc: dict) -> str:
    """Serialize a package document as indent-2 JSON with a trailing newline."""
    return json.dumps(doc, indent=2) + "\n"


@dataclass
class ValcoreMeta:
    """The ``valcore`` block: everything neither foreign format can express.

    ``AgentSpec`` cannot carry a prompt template, required columns, a score space, or tool names,
    so they live here beside the agent rather than being lost in translation.
    """

    prompt_template: str
    required_columns: list[str]
    score_field: str
    score_kind: ScoreKind
    score_labels: list[str] | None
    tools: list[str]

    @classmethod
    def from_dict(cls, data: dict) -> ValcoreMeta:
        """Read a ``valcore`` block, coercing ``score_kind`` back to its enum."""
        return cls(
            prompt_template=data["prompt_template"],
            required_columns=data["required_columns"],
            score_field=data["score_field"],
            score_kind=ScoreKind(data["score_kind"]),
            score_labels=data.get("score_labels"),
            tools=data.get("tools", []),
        )

    def to_dict(self) -> dict:
        """Render the ``valcore`` block, emitting ``score_kind`` as its string value.

        The string form is what ``spec.py`` reads back — ``spec_to_version_fields`` and
        ``_resolve_label_schema`` both compare against the raw value, not the enum.
        """
        return {
            "prompt_template": self.prompt_template,
            "required_columns": self.required_columns,
            "score_field": self.score_field,
            "score_kind": self.score_kind.value,
            "score_labels": self.score_labels,
            "tools": self.tools,
        }


@dataclass
class EvalPackage:
    """An eval package: an optional agent, an optional dataset, and their ``valcore`` block.

    Either half may be absent — a version yields agent + valcore, a dataset yields dataset — and
    ``merge`` recombines a split pair into a whole. Serialization is uniform: ``to_text`` always
    returns a name-to-content mapping, one entry bundled and two split, so callers never branch
    on the mode.
    """

    spec: AgentSpec | None
    dataset: EvalsDataset | None
    valcore: ValcoreMeta | None

    # --- constructors ---------------------------------------------------------

    @classmethod
    def from_version(cls, version: EvaluatorVersion) -> EvalPackage:
        """Build the agent + valcore halves from an evaluator version; no dataset."""
        return cls(
            spec=version_to_spec(version),
            dataset=None,
            valcore=ValcoreMeta.from_dict(valcore_meta(version)),
        )

    @classmethod
    def from_dataset(cls, dataset: VDataset, rows: list[DatasetRow]) -> EvalPackage:
        """Build the dataset half from a valcore dataset and its rows; no agent.

        Evaluators are left empty here — the ``ValcoreJudge`` reference is injected at
        serialization time, where the sibling filename it points at is finally known.
        """
        return cls(spec=None, dataset=dataset_to_evals(dataset, rows, []), valcore=None)

    @classmethod
    def from_text(cls, text: str) -> EvalPackage:
        """Parse an eval-package document, resolving its format from its shape.

        Resolution order: a JSON *array* root is a bare list of cases, then a
        ``valcore/eval-package`` envelope, then a top-level ``cases`` marks a bare pydantic-evals
        dataset, then a top-level ``model``/``instructions`` marks a bare ``AgentSpec``
        (optionally beside a ``valcore`` block). Anything else, an unknown ``kind``, an
        unsupported ``version``, a root that is neither array nor object, or unparseable text is
        a ``ContractError`` naming what was expected.

        The array form is what Logfire's hosted store exports: a case list with no enclosing
        document, which neither valcore nor ``pydantic_evals.Dataset`` accepts as-is. Reading it
        keeps a dataset pushed to Logfire importable again. It carries no dataset name, so the
        result is named ``""`` and the caller supplies one -- ``--name`` for the CLI, the ``name``
        form field for an upload.
        """
        try:
            data = json.loads(text)
        except ValueError as exc:  # JSONDecodeError is a ValueError subclass
            raise ContractError(f"Package text is not valid JSON: {exc}") from exc

        if isinstance(data, list):
            return cls(
                spec=None, dataset=cls._load_dataset({"name": "", "cases": data}), valcore=None
            )

        if not isinstance(data, dict):
            raise ContractError(
                "Package root must be a JSON object or an array of cases, got "
                f"{type(data).__name__}."
            )

        kind = data.get("kind")
        if kind is not None:
            if kind != _KIND:
                raise ContractError(f"Unknown package kind {kind!r}; expected {_KIND!r}.")
            version = data.get("version")
            if version != _VERSION:
                raise ContractError(
                    f"Unsupported package version {version!r}; only version {_VERSION} "
                    "is supported."
                )
            return cls._from_bundled(data)

        if "cases" in data:
            return cls(spec=None, dataset=cls._load_dataset(data), valcore=None)

        if "model" in data or "instructions" in data:
            valcore = data.get("valcore")
            return cls(
                spec=AgentSpec.from_dict(data),
                dataset=None,
                valcore=ValcoreMeta.from_dict(valcore) if valcore is not None else None,
            )

        raise ContractError(
            "Unrecognized document: expected a valcore eval-package (a 'kind' key), a "
            "pydantic-evals dataset (a 'cases' key), or an AgentSpec (a 'model' or "
            "'instructions' key)."
        )

    @classmethod
    def _from_bundled(cls, data: dict) -> EvalPackage:
        """Read the three optional sections of a bundled ``valcore/eval-package`` document."""
        agent = data.get("agent")
        valcore = data.get("valcore")
        dataset = data.get("dataset")
        return cls(
            spec=AgentSpec.from_dict(agent) if agent is not None else None,
            dataset=cls._load_dataset(dataset) if dataset is not None else None,
            valcore=ValcoreMeta.from_dict(valcore) if valcore is not None else None,
        )

    @staticmethod
    def _load_dataset(section: dict) -> EvalsDataset:
        """Load a dataset section, rejecting foreign evaluators and dropping the judge reference.

        Import never reconstructs a foreign evaluator, so an ``evaluators`` entry naming anything
        but ``ValcoreJudge`` is a ``ContractError`` — the check happens here rather than being
        left to pydantic-evals' registry, whose failure is an opaque ``ExceptionGroup``. The
        (validated) evaluators are then stripped before loading so the registry is never
        consulted; the ``ExceptionGroup`` unwrap remains as a defensive fallback.
        """
        for entry in section.get("evaluators") or []:
            name = entry if isinstance(entry, str) else next(iter(entry), None)
            if name != _JUDGE_NAME:
                raise ContractError(
                    f"Package references unsupported evaluator {name!r}; only {_JUDGE_NAME!r} "
                    "can be imported."
                )

        payload = {key: value for key, value in section.items() if key != "evaluators"}
        try:
            return _EvalsDataset.from_dict(payload)
        except BaseExceptionGroup as group:
            messages = "; ".join(str(sub) for sub in group.exceptions)
            raise ContractError(f"Failed to load dataset section: {messages}") from group
        except ValidationError as exc:
            # A malformed case list — the likeliest bad input now that a bare array is accepted —
            # otherwise escapes as a raw pydantic error, which the API maps to a 500 rather than
            # the client error a bad upload deserves.
            raise ContractError(f"Failed to load dataset section: {exc}") from exc

    # --- combination ----------------------------------------------------------

    def merge(self, other: EvalPackage) -> EvalPackage:
        """Combine a split pair, taking the non-``None`` half of each section from either side.

        Raises ``ContractError`` if both sides define the same half — two agents, two datasets,
        or two ``valcore`` blocks cannot be reconciled into one package.
        """
        return EvalPackage(
            spec=self._merge_half(self.spec, other.spec, "agent"),
            dataset=self._merge_half(self.dataset, other.dataset, "dataset"),
            valcore=self._merge_half(self.valcore, other.valcore, "valcore"),
        )

    @staticmethod
    def _merge_half(left: object, right: object, label: str) -> object:
        """Return whichever of the two halves is set, or raise if both are."""
        if left is not None and right is not None:
            raise ContractError(f"Cannot merge: both packages define a {label} section.")
        return left if left is not None else right

    # --- serialization --------------------------------------------------------

    def to_text(self, stem: str, mode: Literal["bundled", "split"] = "bundled") -> dict[str, str]:
        """Serialize the package to a name-to-content mapping.

        Bundled (the default) yields ``{"<stem>.json": ...}`` carrying every present section under
        the ``kind``/``version`` envelope. Split yields up to
        ``{"<stem>.agent.json": ..., "<stem>.dataset.json": ...}``, hoisting each half to the top
        level of its own file and omitting whichever half the package lacks. Content is indent-2
        JSON with a trailing newline.
        """
        if mode == "split":
            return self._to_text_split(stem)
        return self._to_text_bundled(stem)

    def _to_text_bundled(self, stem: str) -> dict[str, str]:
        """Assemble the single-file bundled document."""
        filename = f"{stem}.json"
        doc: dict = {"kind": _KIND, "version": _VERSION}
        if self.spec is not None:
            doc["agent"] = self._agent_dict()
        if self.valcore is not None:
            doc["valcore"] = self.valcore.to_dict()
        if self.dataset is not None:
            # In bundled mode the judge reference points at the bundle's own name.
            doc["dataset"] = self._dataset_dict(filename)
        return {filename: _dumps(doc)}

    def _to_text_split(self, stem: str) -> dict[str, str]:
        """Assemble the split pair, omitting whichever half is absent."""
        agent_name = f"{stem}.agent.json"
        result: dict[str, str] = {}
        if self.spec is not None:
            agent_doc = self._agent_dict()
            # The valcore block sits beside the agent at top level; a bare AgentSpec load ignores
            # it, so the split agent file stays natively loadable.
            if self.valcore is not None:
                agent_doc["valcore"] = self.valcore.to_dict()
            result[agent_name] = _dumps(agent_doc)
        if self.dataset is not None:
            # The judge reference points at the .agent.json sibling.
            result[f"{stem}.dataset.json"] = _dumps(self._dataset_dict(agent_name))
        return result

    def _agent_dict(self) -> dict:
        """Serialize the agent section exactly as ``AgentSpec.to_file`` would.

        ``mode="json"`` with ``by_alias`` and no ``exclude_none`` makes the output byte-for-byte
        indistinguishable from a library-produced spec file.
        """
        assert self.spec is not None
        return self.spec.model_dump(mode="json", by_alias=True)

    def _dataset_dict(self, agent_filename: str) -> dict:
        """Serialize the dataset section, injecting the evaluator reference.

        A package with an agent carries a single ``ValcoreJudge`` reference pointing at the file
        the agent lives in; a dataset with no agent carries an empty list, which keeps it loadable
        by a bare ``Dataset.from_text`` with no ``custom_evaluator_types``.
        """
        assert self.dataset is not None
        doc = self.dataset.model_dump(mode="json", by_alias=True)
        if self.spec is not None:
            doc["evaluators"] = [{_JUDGE_NAME: {"package": agent_filename}}]
        else:
            doc["evaluators"] = []
        return doc

    # --- decoding to valcore fields -------------------------------------------

    def to_version_fields(self) -> dict:
        """Reconstruct evaluator-version field values from the agent + valcore halves."""
        if self.spec is None or self.valcore is None:
            raise ContractError(
                "Package has no agent section to reconstruct an evaluator version from."
            )
        return spec_to_version_fields(self.spec, self.valcore.to_dict())

    def to_dataset_fields(self) -> tuple[str, list[str], dict, list[dict]]:
        """Decode the dataset half into ``(name, columns, label_schema, prepared_rows)``.

        The ``valcore`` block, when present, wins over inference of the label schema, so a full
        package preserves its declared score space rather than re-deriving it from the labels.
        """
        if self.dataset is None:
            raise ContractError("Package has no dataset section.")
        valcore = self.valcore.to_dict() if self.valcore is not None else None
        return evals_to_dataset_fields(self.dataset, valcore)
