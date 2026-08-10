"""The canonical translation layer between valcore and the two foreign models.

This is the single place where either ``pydantic_ai.agent.spec.AgentSpec`` or
``pydantic_evals.Dataset`` is constructed or read. Both renderers (code and config) and the
importer route through here, so a change to how valcore maps onto either foreign format lives
in exactly one module rather than being re-derived at every call site.

The two ``Dataset`` names are aliased on import — ``valcore.models.Dataset`` as ``VDataset`` and
``pydantic_evals.Dataset`` as ``EvalsDataset`` — because confusing valcore's storage entity with
pydantic-evals' serialization format is the single most likely bug in this translation.

This module returns objects, never text: JSON serialization is ``config_io``'s job. It does not
import ``yaml``, touch the store, read the environment, or use ``Dataset.to_file`` /
``from_file``.
"""

from typing import Any, Literal

from pydantic import create_model
from pydantic_ai.agent.spec import AgentSpec
from pydantic_evals import Dataset as EvalsDataset
from pydantic_evals.dataset import Case

from valcore.capabilities import VALID_CAPABILITIES
from valcore.errors import ContractError
from valcore.factory import build_output_model
from valcore.models import Dataset as VDataset
from valcore.models import DatasetRow, EvaluatorVersion

# The provenance keys copied between a valcore row and a case's ``valcore_row`` metadata block.
# ``idx`` is written on export but never read back on import: ``add_prepared_rows`` regenerates
# sequential indices, so a stored ``idx`` would collide with the keyword it assigns.
_PROVENANCE_KEYS: tuple[str, ...] = ("note", "label_reasoning", "label_source", "suggested_label")

# Mirror ``api/routes/datasets.py``'s ``_JSONL_INFER_LIMIT``: inspect at most this many cases
# when inferring the column union, so a late outlier case cannot silently widen the schema.
_INFER_LIMIT = 50


# --- output_fields <-> output_schema -----------------------------------------


def output_fields_to_schema(version: EvaluatorVersion) -> dict:
    """Encode a version's output fields as a JSON Schema.

    The forward direction is never hand-rolled: the model factory already builds the exact
    Pydantic model valcore runs, so its ``model_json_schema()`` is by construction the schema a
    reloaded ``AgentSpec`` will reproduce.
    """
    return build_output_model(version).model_json_schema()


def _resolve_scalar(field_name: str, prop: dict) -> dict:
    """Map a single (non-optional) JSON Schema property to its ``OutputField`` type keys.

    Raises ContractError naming the field for any construct outside the lossless scalar map —
    ``$ref``/``allOf``/``oneOf``, a nested object, or an array — since those cannot be expressed
    as a valcore output field and would otherwise be silently dropped.
    """
    for construct in ("$ref", "allOf", "oneOf"):
        if construct in prop:
            raise ContractError(
                f"Output field {field_name!r} uses unsupported schema construct {construct!r}."
            )

    jtype = prop.get("type")
    if jtype == "string":
        if "enum" in prop:
            return {"type": "enum", "enum_values": list(prop["enum"])}
        return {"type": "str"}
    if jtype == "integer":
        return {"type": "int"}
    if jtype == "number":
        result: dict = {"type": "float"}
        if "minimum" in prop:
            result["minimum"] = prop["minimum"]
        if "maximum" in prop:
            result["maximum"] = prop["maximum"]
        return result
    if jtype == "boolean":
        return {"type": "bool"}
    if jtype == "object":
        raise ContractError(
            f"Output field {field_name!r} uses unsupported schema construct 'nested object'."
        )
    if jtype == "array":
        raise ContractError(
            f"Output field {field_name!r} uses unsupported schema construct 'array'."
        )
    raise ContractError(f"Output field {field_name!r} has unsupported schema type {jtype!r}.")


def schema_to_output_fields(schema: dict) -> list[dict]:
    """Decode a JSON Schema object back into ordered ``OutputField`` dicts.

    The reverse of ``output_fields_to_schema``, exact for everything valcore emits. Property
    insertion order is preserved as field order; ``title`` is ignored; a required entry becomes
    ``required: true`` and a nullable ``anyOf`` collapses to its non-null branch with
    ``required: false``.
    """
    required = set(schema.get("required", []))
    fields: list[dict] = []
    for name, prop in schema.get("properties", {}).items():
        if "anyOf" in prop:
            branches = [b for b in prop["anyOf"] if b.get("type") != "null"]
            if len(branches) != 1:
                raise ContractError(
                    f"Output field {name!r} uses an unsupported 'anyOf' with "
                    f"{len(branches)} non-null branches."
                )
            type_keys = _resolve_scalar(name, branches[0])
        else:
            type_keys = _resolve_scalar(name, prop)

        field: dict = {"name": name, **type_keys}
        field["description"] = prop.get("description", "")
        field["required"] = name in required
        fields.append(field)
    return fields


# --- capabilities -------------------------------------------------------------


def _capabilities_to_spec(capabilities: list[dict]) -> list[dict]:
    """Translate valcore ``{name, config}`` capabilities to ``AgentSpec`` ``{name, arguments}``."""
    result: list[dict] = []
    for cap in capabilities:
        name = cap.get("name")
        if name not in VALID_CAPABILITIES:
            raise ContractError(
                f"Unknown capability {name!r}; valid names are {sorted(VALID_CAPABILITIES)}."
            )
        result.append({"name": name, "arguments": cap.get("config", {})})
    return result


def _capabilities_from_spec(spec: AgentSpec) -> list[dict]:
    """Translate an ``AgentSpec``'s capabilities back to valcore ``{name, config}`` dicts."""
    result: list[dict] = []
    for cap in spec.capabilities:
        if cap.name not in VALID_CAPABILITIES:
            raise ContractError(
                f"Unknown capability {cap.name!r}; valid names are {sorted(VALID_CAPABILITIES)}."
            )
        result.append({"name": cap.name, "config": dict(cap.arguments or {})})
    return result


# --- EvaluatorVersion <-> AgentSpec -------------------------------------------


def version_to_spec(version: EvaluatorVersion) -> AgentSpec:
    """Build the ``AgentSpec`` for a version's ``agent`` section.

    Carries model, name, instructions, the lossless ``output_schema``, and translated
    capabilities. Tools are deliberately absent: ``AgentSpec`` has no ``tools`` field and would
    silently ignore one, so valcore's tool names live only in the package's ``valcore`` block.
    """
    return AgentSpec(
        model=version.model,
        name=version.version_name,
        instructions=version.instructions,
        output_schema=output_fields_to_schema(version),
        capabilities=_capabilities_to_spec(version.capabilities),
    )


def spec_to_version_fields(spec: AgentSpec, valcore: dict) -> dict:
    """Reconstruct evaluator-version field values from an ``AgentSpec`` plus its ``valcore`` block.

    The ``AgentSpec`` supplies model, name, instructions, output fields, and capabilities; the
    ``valcore`` block supplies everything the foreign format cannot express — prompt template,
    required columns, score space, and tool names.
    """
    return {
        "model": spec.model,
        "version_name": spec.name,
        "instructions": spec.instructions,
        "output_fields": schema_to_output_fields(spec.output_schema),
        "capabilities": _capabilities_from_spec(spec),
        "prompt_template": valcore["prompt_template"],
        "required_columns": valcore["required_columns"],
        "score_field": valcore["score_field"],
        "score_kind": valcore["score_kind"],
        "score_labels": valcore.get("score_labels"),
        "tools": valcore.get("tools", []),
    }


def valcore_meta(version: EvaluatorVersion) -> dict:
    """Produce the package's ``valcore`` block — the fields no foreign format can express."""
    return {
        "prompt_template": version.prompt_template,
        "required_columns": version.required_columns,
        "score_field": version.score_field,
        "score_kind": version.score_kind.value,
        "score_labels": version.score_labels,
        "tools": version.tools,
    }


# --- Dataset <-> pydantic_evals.Dataset ---------------------------------------


def _row_to_case(row: DatasetRow, wrap_output: bool = False) -> Case:
    """Map one valcore row to a pydantic-evals case, carrying provenance in ``valcore_row``.

    ``wrap_output`` emits ``expected_output`` as ``{"value": label}`` instead of the bare label.
    Logfire's hosted datasets API types that field as a dictionary -- a scalar is rejected with
    ``dict_type: Input should be a valid dictionary``, and its client only passes dicts through
    untouched, serializing anything else as-is. ``{"value": ...}`` is valcore's own storage shape
    for ``DatasetRow.label``, so the hosted form matches the database rather than inventing a
    third representation. Local exports stay scalar for ``EqualsExpected``.
    """
    valcore_row: dict = {"idx": row.idx}
    if row.note is not None:
        valcore_row["note"] = row.note
    if row.label_reasoning is not None:
        valcore_row["label_reasoning"] = row.label_reasoning
    if row.label_source is not None:
        valcore_row["label_source"] = row.label_source.value
    if row.suggested_label is not None:
        valcore_row["suggested_label"] = row.suggested_label

    # A row with no label omits ``expected_output`` rather than asserting a null ground truth.
    expected = None
    if row.label is not None:
        expected = {"value": row.label["value"]} if wrap_output else row.label["value"]
    return Case(
        name=row.id,
        inputs=row.data,
        expected_output=expected,
        metadata={"valcore_row": valcore_row},
    )


def _output_type(dataset: VDataset, wrap: bool = False) -> Any:
    """Derive ``OutputT`` from the dataset's label schema so a hosted push carries a real schema.

    A bare ``object`` infers to ``{}`` in ``TypeAdapter(...).json_schema()``, which is what
    ``push_dataset`` reads to build the hosted expected-output schema. valcore knows the label
    space exactly, so it is encoded here rather than left to infer to nothing.

    With ``wrap``, the scalar is wrapped in a single-field model so the inferred schema is an
    *object*. Logfire's hosted datasets API requires ``expected_output`` to be a dictionary and
    rejects a scalar with ``dict_type: Input should be a valid dictionary``; see
    :func:`_row_to_case`. Only the hosted push wraps -- the exported ``pydantic_evals`` dataset
    keeps a scalar, which is what ``EqualsExpected`` compares against.
    """
    kind = dataset.label_schema.get("kind")
    if kind == "categorical":
        labels = dataset.label_schema.get("labels") or []
        inner: Any = Literal[tuple(labels)] if labels else str  # type: ignore[valid-type]
    elif kind == "numeric":
        inner = float
    else:
        inner = str

    if not wrap:
        return inner
    return create_model("ExpectedOutput", value=(inner, ...))


def dataset_to_evals(
    dataset: VDataset,
    rows: list[DatasetRow],
    evaluators: list[dict],
    *,
    wrap_output: bool = False,
) -> EvalsDataset:
    """Map a valcore dataset and its rows onto a ``pydantic_evals.Dataset``.

    Concrete generics are used deliberately: constructing ``EvalsDataset`` with unparameterized
    generics emits a ``UserWarning``. ``OutputT`` is derived from the dataset's label schema
    rather than left as ``object`` so a hosted push infers a real expected-output schema.

    ``wrap_output`` shapes the result for Logfire's hosted datasets API, which requires
    ``expected_output`` to be an object; see :func:`_row_to_case`. It defaults off so exported
    ``.dataset.json`` files keep the scalar form that ``EqualsExpected`` compares against.
    """
    cases = [_row_to_case(row, wrap_output=wrap_output) for row in rows]
    output_type = _output_type(dataset, wrap=wrap_output)
    return EvalsDataset[dict[str, Any], output_type, dict[str, Any]](  # type: ignore[valid-type]
        name=dataset.name, cases=cases, evaluators=evaluators
    )


def _infer_columns(cases: list[Case]) -> list[str]:
    """Infer the column union from the first ``_INFER_LIMIT`` cases, in first-seen order.

    A scalar ``inputs`` contributes the single synthetic column ``input`` (the same wrapping
    applied when preparing its row), matching how such a case is imported.
    """
    columns: list[str] = []
    for case in cases[:_INFER_LIMIT]:
        keys = case.inputs.keys() if isinstance(case.inputs, dict) else ["input"]
        for key in keys:
            if key not in columns:
                columns.append(key)
    return columns


def _expected_label(case: Case) -> Any:
    """Return a case's ground-truth label, accepting both shapes ``expected_output`` arrives in.

    valcore's own export writes a scalar. A dataset round-tripped through Logfire's hosted store
    comes back wrapped as ``{"value": ...}``, because that API requires an object and rejects a
    scalar (see :func:`_row_to_case`). Unwrapping in one place means schema inference and the
    stored label cannot disagree about which shape they are reading.

    Only a lone ``value`` key is unwrapped: a richer dict is a genuine structured label and is
    kept whole rather than being guessed at.
    """
    expected = case.expected_output
    if isinstance(expected, dict) and set(expected) == {"value"}:
        return expected["value"]
    return expected


def _resolve_label_schema(cases: list[Case], valcore: dict | None) -> dict:
    """Resolve the dataset's label schema, preferring an explicit ``valcore`` block.

    Falls back to inferring from the cases' ``expected_output`` values — categorical when they
    are all strings, numeric when they are all numbers — and finally to the empty schema, the
    legal "no ground truth" state that leaves the dataset runnable for EVAL runs.
    """
    if valcore and valcore.get("score_kind"):
        kind = valcore["score_kind"]
        if kind == "categorical":
            return {"kind": "categorical", "labels": valcore.get("score_labels")}
        return {"kind": "numeric"}

    labels = [label for label in (_expected_label(c) for c in cases) if label is not None]
    if not labels:
        return {}
    if all(isinstance(label, str) for label in labels):
        return {"kind": "categorical", "labels": sorted(set(labels))}
    if all(isinstance(label, (int, float)) and not isinstance(label, bool) for label in labels):
        return {"kind": "numeric", "minimum": min(labels), "maximum": max(labels)}
    return {}


def _case_to_prepared(case: Case) -> dict:
    """Map one pydantic-evals case to the prepared-row dict ``Store.add_prepared_rows`` accepts."""
    data = case.inputs if isinstance(case.inputs, dict) else {"input": case.inputs}
    fields: dict = {"data": data}
    label = _expected_label(case)
    if label is not None:
        fields["label"] = {"value": label}

    valcore_row = (case.metadata or {}).get("valcore_row") or {}
    for key in _PROVENANCE_KEYS:
        if valcore_row.get(key) is not None:
            fields[key] = valcore_row[key]
    return fields


def evals_to_dataset_fields(
    ds: EvalsDataset, valcore: dict | None
) -> tuple[str, list[str], dict, list[dict]]:
    """Decode a ``pydantic_evals.Dataset`` into valcore dataset fields.

    Returns ``(name, columns, label_schema, prepared_rows)`` where ``prepared_rows`` is the
    shape ``Store.add_prepared_rows`` already accepts. Case ``name``s are ignored — row ids are
    regenerated on import.
    """
    cases = list(ds.cases)
    columns = _infer_columns(cases)
    label_schema = _resolve_label_schema(cases, valcore)
    prepared = [_case_to_prepared(case) for case in cases]
    return ds.name, columns, label_schema, prepared
