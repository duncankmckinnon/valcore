"""Tests for the canonical translation layer between valcore and the two foreign models.

``spec.py`` is the one place either ``pydantic_ai.agent.spec.AgentSpec`` or
``pydantic_evals.Dataset`` is constructed or read. These tests pin the exact translations —
the reverse ``output_schema`` map, capability round trips, and row/case mapping — that both the
renderers and the importer depend on, before any of that code exists.
"""

import pytest
from pydantic_ai.agent.spec import AgentSpec
from pydantic_evals import Dataset as EvalsDataset
from pydantic_evals.dataset import Case

from valcore.errors import ContractError
from valcore.factory import build_output_model
from valcore.models import Dataset as VDataset
from valcore.models import (
    DatasetRow,
    EvaluatorVersion,
    LabelSource,
    ScoreKind,
)
from valcore.spec import (
    dataset_to_evals,
    evals_to_dataset_fields,
    output_fields_to_schema,
    schema_to_output_fields,
    spec_to_version_fields,
    valcore_meta,
    version_to_spec,
)


def make_version(**overrides: object) -> EvaluatorVersion:
    """Build a valid categorical EvaluatorVersion, applying any field overrides."""
    base: dict[str, object] = {
        "evaluator_id": "ev1",
        "version_name": "refusal judge",
        "model": "openai:gpt-4o",
        "instructions": "You judge whether an answer is a refusal.",
        "prompt_template": "Q: {question}\nA: {answer}",
        "required_columns": ["question", "answer"],
        "output_fields": [
            {
                "name": "score",
                "type": "enum",
                "description": "the judgment",
                "enum_values": ["refusal", "partial", "answer"],
            }
        ],
        "score_field": "score",
        "score_kind": ScoreKind.CATEGORICAL,
        "score_labels": ["refusal", "partial", "answer"],
        "capabilities": [],
        "tools": ["regex_search"],
    }
    base.update(overrides)
    return EvaluatorVersion(**base)


def base_schema(prop: dict, *, required: bool = True) -> dict:
    """Wrap a single property named 'field' into a top-level object schema."""
    return {
        "type": "object",
        "properties": {"field": prop},
        "required": ["field"] if required else [],
    }


# --- output_schema reverse map ------------------------------------------------


def test_reverse_map_enum() -> None:
    schema = base_schema({"type": "string", "enum": ["a", "b"], "description": "an enum"})
    fields = schema_to_output_fields(schema)
    assert len(fields) == 1
    field = fields[0]
    assert field["name"] == "field"
    assert field["type"] == "enum"
    assert field["enum_values"] == ["a", "b"]
    assert field["description"] == "an enum"
    assert field["required"] is True


def test_reverse_map_str() -> None:
    field = schema_to_output_fields(base_schema({"type": "string", "description": "s"}))[0]
    assert field["type"] == "str"
    assert field["description"] == "s"


def test_reverse_map_int() -> None:
    field = schema_to_output_fields(base_schema({"type": "integer", "description": "i"}))[0]
    assert field["type"] == "int"


def test_reverse_map_float_with_bounds() -> None:
    field = schema_to_output_fields(
        base_schema({"type": "number", "description": "r", "minimum": 0.0, "maximum": 1.0})
    )[0]
    assert field["type"] == "float"
    assert field["minimum"] == 0.0
    assert field["maximum"] == 1.0


def test_reverse_map_float_without_bounds_omits_bounds() -> None:
    field = schema_to_output_fields(base_schema({"type": "number", "description": "r"}))[0]
    assert field["type"] == "float"
    assert field.get("minimum") is None
    assert field.get("maximum") is None


def test_reverse_map_bool() -> None:
    field = schema_to_output_fields(base_schema({"type": "boolean", "description": "b"}))[0]
    assert field["type"] == "bool"


def test_reverse_map_optional_via_anyof() -> None:
    schema = base_schema(
        {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "maybe"},
        required=False,
    )
    field = schema_to_output_fields(schema)[0]
    assert field["type"] == "str"
    assert field["required"] is False


def test_reverse_map_description_defaults_to_empty_string() -> None:
    field = schema_to_output_fields(base_schema({"type": "string"}))[0]
    assert field["description"] == ""


def test_reverse_map_missing_from_required_is_not_required() -> None:
    field = schema_to_output_fields(base_schema({"type": "string"}, required=False))[0]
    assert field["required"] is False


# --- field order --------------------------------------------------------------


def test_field_order_survives_round_trip() -> None:
    version = make_version(
        output_fields=[
            {"name": "score", "type": "int", "description": "s"},
            {"name": "reason", "type": "str", "description": "why"},
            {"name": "ok", "type": "bool", "description": "b"},
        ],
        score_field="score",
        score_kind=ScoreKind.NUMERIC,
        score_labels=None,
    )
    schema = output_fields_to_schema(version)
    round_tripped = schema_to_output_fields(schema)
    assert [f["name"] for f in round_tripped] == ["score", "reason", "ok"]


def test_output_fields_to_schema_matches_build_output_model() -> None:
    version = make_version()
    assert output_fields_to_schema(version) == build_output_model(version).model_json_schema()


# --- unsupported schema constructs --------------------------------------------


@pytest.mark.parametrize(
    "prop",
    [
        pytest.param({"$ref": "#/$defs/Foo"}, id="ref"),
        pytest.param({"allOf": [{"type": "string"}]}, id="allOf"),
        pytest.param({"oneOf": [{"type": "string"}, {"type": "integer"}]}, id="oneOf"),
        pytest.param({"type": "object", "properties": {}}, id="nested-object"),
        pytest.param({"type": "array", "items": {"type": "string"}}, id="array"),
    ],
)
def test_unsupported_construct_raises_contract_error_naming_field(prop: dict) -> None:
    with pytest.raises(ContractError) as exc:
        schema_to_output_fields(base_schema(prop))
    assert "field" in str(exc.value)


# --- capability translation ---------------------------------------------------


def test_version_to_spec_translates_capability_forward() -> None:
    version = make_version(capabilities=[{"name": "CodeMode", "config": {"max_retries": 2}}])
    spec = version_to_spec(version)
    dumped = spec.model_dump(by_alias=True)
    assert [c["name"] for c in dumped["capabilities"]] == ["CodeMode"]
    assert dumped["capabilities"][0]["arguments"] == {"max_retries": 2}


def test_spec_to_version_fields_translates_capability_backward() -> None:
    spec = version_to_spec(make_version(capabilities=[{"name": "CodeMode", "config": {}}]))
    fields = spec_to_version_fields(spec, valcore_meta(make_version()))
    assert fields["capabilities"] == [{"name": "CodeMode", "config": {}}]


def test_version_to_spec_rejects_unknown_capability_name() -> None:
    version = make_version(capabilities=[{"name": "Bogus", "config": {}}])
    with pytest.raises(ContractError) as exc:
        version_to_spec(version)
    assert "Bogus" in str(exc.value)


# --- version_to_spec ----------------------------------------------------------


def test_version_to_spec_is_reloadable_and_carries_schema() -> None:
    version = make_version()
    spec = version_to_spec(version)
    assert isinstance(spec, AgentSpec)
    # A spec valcore emits must survive a bare pydantic-ai reload.
    reloaded = AgentSpec.from_dict(spec.model_dump(by_alias=True))
    assert isinstance(reloaded, AgentSpec)
    assert spec.output_schema == build_output_model(version).model_json_schema()
    assert spec.model == version.model
    assert spec.name == version.version_name
    assert spec.instructions == version.instructions


def test_version_to_spec_has_no_tools_field() -> None:
    # AgentSpec cannot express tools; the mistake would be silent since it ignores extras.
    spec = version_to_spec(make_version())
    assert "tools" not in spec.model_dump(by_alias=True)


# --- valcore_meta -------------------------------------------------------------


def test_valcore_meta_produces_the_valcore_block() -> None:
    version = make_version()
    meta = valcore_meta(version)
    assert meta["prompt_template"] == version.prompt_template
    assert meta["required_columns"] == version.required_columns
    assert meta["score_field"] == "score"
    assert meta["score_kind"] == "categorical"
    assert meta["score_labels"] == ["refusal", "partial", "answer"]
    assert meta["tools"] == ["regex_search"]


# --- row <-> case mapping -----------------------------------------------------


def make_rows() -> list[DatasetRow]:
    """Two rows: one fully labeled with a note, one with no label at all."""
    return [
        DatasetRow(
            dataset_id="d1",
            idx=0,
            data={"question": "Q1", "answer": "A1"},
            label={"value": "refusal"},
            label_source=LabelSource.MANUAL,
            note="a note",
        ),
        DatasetRow(
            dataset_id="d1",
            idx=1,
            data={"question": "Q2", "answer": "A2"},
        ),
    ]


def test_dataset_to_evals_maps_rows_to_cases() -> None:
    dataset = VDataset(name="refusal-quality", columns=["question", "answer"])
    evals = dataset_to_evals(dataset, make_rows(), [])
    assert isinstance(evals, EvalsDataset)
    assert evals.name == "refusal-quality"

    labeled, _unlabeled = evals.cases
    assert labeled.inputs == {"question": "Q1", "answer": "A1"}
    assert labeled.expected_output == "refusal"
    valcore_row = labeled.metadata["valcore_row"]
    assert valcore_row["idx"] == 0
    assert valcore_row["note"] == "a note"
    assert valcore_row["label_source"] == "manual"
    # Only non-None provenance keys are written.
    assert "label_reasoning" not in valcore_row
    assert "suggested_label" not in valcore_row


def test_dataset_to_evals_row_id_becomes_case_name() -> None:
    rows = make_rows()
    dataset = VDataset(name="d", columns=["question", "answer"])
    evals = dataset_to_evals(dataset, rows, [])
    assert evals.cases[0].name == rows[0].id


def test_dataset_to_evals_unlabeled_row_has_no_expected_output() -> None:
    dataset = VDataset(name="d", columns=["question", "answer"])
    evals = dataset_to_evals(dataset, make_rows(), [])
    assert evals.cases[1].expected_output is None


def test_row_case_round_trip_preserves_data_label_source_and_note() -> None:
    dataset = VDataset(name="refusal-quality", columns=["question", "answer"])
    evals = dataset_to_evals(dataset, make_rows(), [])
    name, columns, _label_schema, prepared = evals_to_dataset_fields(
        evals, valcore_meta(make_version())
    )

    assert name == "refusal-quality"
    assert columns == ["question", "answer"]

    labeled, unlabeled = prepared
    assert labeled["data"] == {"question": "Q1", "answer": "A1"}
    assert labeled["label"] == {"value": "refusal"}
    assert labeled["label_source"] == "manual"
    assert labeled["note"] == "a note"
    # An unlabeled row omits the label key entirely rather than emitting label=None.
    assert "label" not in unlabeled


# --- scalar inputs ------------------------------------------------------------


def test_scalar_inputs_wrapped_on_import() -> None:
    ds = EvalsDataset[str, str, dict](
        name="s", cases=[Case(name="r", inputs="hello", expected_output="refusal")]
    )
    _name, columns, _schema, prepared = evals_to_dataset_fields(ds, None)
    assert columns == ["input"]
    assert prepared[0]["data"] == {"input": "hello"}


# --- column inference ---------------------------------------------------------


def test_column_inference_takes_first_seen_order() -> None:
    ds = EvalsDataset[dict, str, dict](
        name="c",
        cases=[
            Case(name="1", inputs={"a": 1}),
            Case(name="2", inputs={"b": 2, "a": 3}),
            Case(name="3", inputs={"c": 4}),
        ],
    )
    _name, columns, _schema, _prepared = evals_to_dataset_fields(ds, None)
    assert columns == ["a", "b", "c"]


def test_column_inference_stops_at_fifty_cases() -> None:
    cases = [Case(name=str(i), inputs={"q": i}) for i in range(50)]
    # The 51st case introduces a new key, which must be ignored by the 50-case inference limit.
    cases.append(Case(name="late", inputs={"q": 50, "late": 1}))
    ds = EvalsDataset[dict, str, dict](name="c", cases=cases)
    _name, columns, _schema, _prepared = evals_to_dataset_fields(ds, None)
    assert columns == ["q"]


# --- label schema resolution --------------------------------------------------


def test_label_schema_from_explicit_valcore_block() -> None:
    ds = EvalsDataset[dict, str, dict](
        name="c",
        cases=[Case(name="1", inputs={"q": "x"}, expected_output="refusal")],
    )
    valcore = {"score_kind": "categorical", "score_labels": ["refusal", "answer"]}
    _name, _columns, label_schema, _prepared = evals_to_dataset_fields(ds, valcore)
    assert label_schema["kind"] == "categorical"
    assert label_schema["labels"] == ["refusal", "answer"]


def test_label_schema_inferred_categorical() -> None:
    ds = EvalsDataset[dict, str, dict](
        name="c",
        cases=[
            Case(name="1", inputs={"q": "x"}, expected_output="b"),
            Case(name="2", inputs={"q": "y"}, expected_output="a"),
            Case(name="3", inputs={"q": "z"}, expected_output="a"),
        ],
    )
    _name, _columns, label_schema, _prepared = evals_to_dataset_fields(ds, None)
    assert label_schema["kind"] == "categorical"
    assert label_schema["labels"] == ["a", "b"]


def test_label_schema_inferred_numeric() -> None:
    ds = EvalsDataset[dict, int, dict](
        name="c",
        cases=[
            Case(name="1", inputs={"q": "x"}, expected_output=1),
            Case(name="2", inputs={"q": "y"}, expected_output=5),
            Case(name="3", inputs={"q": "z"}, expected_output=3),
        ],
    )
    _name, _columns, label_schema, _prepared = evals_to_dataset_fields(ds, None)
    assert label_schema["kind"] == "numeric"
    assert label_schema["minimum"] == 1
    assert label_schema["maximum"] == 5


def test_label_schema_empty_when_no_case_has_a_label() -> None:
    ds = EvalsDataset[dict, str, dict](
        name="c",
        cases=[Case(name="1", inputs={"q": "x"}), Case(name="2", inputs={"q": "y"})],
    )
    _name, _columns, label_schema, _prepared = evals_to_dataset_fields(ds, None)
    assert label_schema == {}
