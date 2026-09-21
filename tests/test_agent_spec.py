"""Tests for pure validation and derivation over stored AgentSpec blobs."""

import pytest
from pydantic_ai.agent.spec import AgentSpec
from valcore.agent_spec import (
    build_deps,
    capability_names,
    deps_properties,
    output_column_names,
    parse_spec,
    render_agent_prompt,
    required_deps_properties,
    validate_binding,
)

from valcore.errors import ConfigError, ContractError


def full_blob(**overrides: object) -> dict[str, object]:
    """A full agent spec blob with deps_schema, output_schema, and capabilities."""
    base: dict[str, object] = {
        "instructions": "You are a helpful assistant.",
        "deps_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["topic"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string"},
                "confidence": {"type": "number"},
            },
        },
        "capabilities": ["WebSearch", {"FileSystem": {}}],
    }
    base.update(overrides)
    return base


# --- parse_spec ------------------------------------------------------------


class TestParseSpec:
    """AgentSpec.from_dict wrapped with domain errors instead of raw validation errors."""

    def test_accepts_minimal_blob(self) -> None:
        spec = parse_spec({"instructions": "hi"})
        assert isinstance(spec, AgentSpec)
        assert spec.instructions == "hi"

    def test_accepts_full_blob(self) -> None:
        spec = parse_spec(full_blob())
        assert isinstance(spec, AgentSpec)
        assert spec.deps_schema is not None
        assert spec.output_schema is not None
        assert [c.name for c in spec.capabilities] == ["WebSearch", "FileSystem"]

    def test_raises_config_error_not_validation_error(self) -> None:
        with pytest.raises(ConfigError) as exc:
            parse_spec({"capabilities": 5})
        assert "Invalid agent spec" in str(exc.value)

    def test_config_error_does_not_leak_validation_error_type(self) -> None:
        # A bare ValidationError escaping would become an unhandled 500 at the API layer.
        try:
            parse_spec({"capabilities": 5})
        except ConfigError:
            pass
        except Exception as exc:  # noqa: BLE001 - explicitly asserting this doesn't happen
            pytest.fail(f"expected ConfigError, got {type(exc).__name__}: {exc}")


# --- capability_names --------------------------------------------------------


class TestCapabilityNames:
    """Extracting bare capability names from a parsed spec."""

    def test_empty_when_no_capabilities(self) -> None:
        spec = parse_spec({"instructions": "hi"})
        assert capability_names(spec) == []

    def test_lists_names_in_order(self) -> None:
        spec = parse_spec(full_blob())
        assert capability_names(spec) == ["WebSearch", "FileSystem"]


# --- deps_properties / required_deps_properties -----------------------------


class TestDepsProperties:
    """Reading deps_schema's properties and required fields defensively."""

    def test_empty_dict_when_no_deps_schema(self) -> None:
        spec = parse_spec({"instructions": "hi"})
        assert deps_properties(spec) == {}

    def test_returns_properties_from_deps_schema(self) -> None:
        spec = parse_spec(full_blob())
        props = deps_properties(spec)
        assert set(props) == {"topic", "max_results"}

    def test_empty_set_when_no_deps_schema(self) -> None:
        spec = parse_spec({"instructions": "hi"})
        assert required_deps_properties(spec) == set()

    def test_returns_required_from_deps_schema(self) -> None:
        spec = parse_spec(full_blob())
        assert required_deps_properties(spec) == {"topic"}


# --- output_column_names -----------------------------------------------------


class TestOutputColumnNames:
    """Deriving dataset column names an agent's output occupies."""

    def test_defaults_to_response_with_no_output_schema(self) -> None:
        spec = parse_spec({"instructions": "hi"})
        assert output_column_names(spec) == ["response"]

    def test_honours_custom_text_column(self) -> None:
        spec = parse_spec({"instructions": "hi"})
        assert output_column_names(spec, text_column="answer") == ["answer"]

    def test_one_name_per_property_for_structured_schema(self) -> None:
        spec = parse_spec(full_blob())
        assert output_column_names(spec) == ["verdict", "confidence"]

    def test_falls_back_to_text_column_when_schema_has_no_properties(self) -> None:
        spec = parse_spec(full_blob(output_schema={"type": "object"}))
        assert output_column_names(spec) == ["response"]

    def test_falls_back_to_custom_text_column_when_schema_has_no_properties(self) -> None:
        spec = parse_spec(full_blob(output_schema={"type": "object"}))
        assert output_column_names(spec, text_column="answer") == ["answer"]


# --- validate_binding ---------------------------------------------------------


class TestValidateBinding:
    """The five failure modes for a binding against a parsed spec, plus the happy path."""

    def test_passes_for_consistent_binding(self) -> None:
        spec = parse_spec(full_blob())
        validate_binding(
            spec,
            prompt_template="Discuss {topic} for {audience}.",
            required_columns=["topic", "audience"],
            deps_mapping={"topic": "topic", "max_results": "audience"},
        )

    def test_empty_required_columns_raises(self) -> None:
        spec = parse_spec(full_blob())
        with pytest.raises(ConfigError) as exc:
            validate_binding(
                spec,
                prompt_template="hi",
                required_columns=[],
                deps_mapping={},
            )
        assert "at least one required column" in str(exc.value)

    def test_prompt_placeholder_not_in_required_columns_raises(self) -> None:
        spec = parse_spec(full_blob())
        with pytest.raises(ConfigError) as exc:
            validate_binding(
                spec,
                prompt_template="Discuss {topic} and {mystery}.",
                required_columns=["topic"],
                deps_mapping={"topic": "topic"},
            )
        assert "mystery" in str(exc.value)

    def test_deps_mapping_key_not_a_deps_property_raises(self) -> None:
        spec = parse_spec(full_blob())
        with pytest.raises(ConfigError) as exc:
            validate_binding(
                spec,
                prompt_template="Discuss {topic}.",
                required_columns=["topic"],
                deps_mapping={"not_a_property": "topic"},
            )
        assert "not_a_property" in str(exc.value)

    def test_deps_mapping_value_not_in_required_columns_raises(self) -> None:
        spec = parse_spec(full_blob())
        with pytest.raises(ConfigError) as exc:
            validate_binding(
                spec,
                prompt_template="Discuss {topic}.",
                required_columns=["topic"],
                deps_mapping={"topic": "not_a_required_column"},
            )
        assert "not_a_required_column" in str(exc.value)

    def test_unmapped_required_deps_property_raises(self) -> None:
        spec = parse_spec(full_blob())
        with pytest.raises(ConfigError) as exc:
            validate_binding(
                spec,
                prompt_template="Discuss {topic}.",
                required_columns=["topic"],
                deps_mapping={},
            )
        assert "topic" in str(exc.value)

    def test_passes_with_no_deps_schema_and_empty_mapping(self) -> None:
        spec = parse_spec({"instructions": "hi"})
        validate_binding(
            spec,
            prompt_template="Discuss {topic}.",
            required_columns=["topic"],
            deps_mapping={},
        )


# --- build_deps ----------------------------------------------------------------


class TestBuildDeps:
    """Mapping dataset columns onto agent deps, preserving JSON types."""

    def test_maps_columns_to_deps_fields(self) -> None:
        deps = build_deps({"topic": "subject_col"}, {"subject_col": "space"})
        assert deps == {"topic": "space"}

    def test_preserves_non_string_types(self) -> None:
        deps = build_deps({"max_results": "limit_col"}, {"limit_col": 5})
        assert deps == {"max_results": 5}
        assert isinstance(deps["max_results"], int)

    def test_preserves_falsy_and_complex_values(self) -> None:
        deps = build_deps(
            {"count": "count_col", "flag": "flag_col", "items": "items_col"},
            {"count_col": 0, "flag_col": False, "items_col": [1, 2, 3]},
        )
        assert deps == {"count": 0, "flag": False, "items": [1, 2, 3]}

    def test_missing_column_raises_contract_error(self) -> None:
        with pytest.raises(ContractError) as exc:
            build_deps({"topic": "subject_col"}, {"other_col": "space"})
        assert "subject_col" in str(exc.value)

    def test_empty_mapping_returns_empty_deps(self) -> None:
        assert build_deps({}, {"anything": "value"}) == {}


# --- render_agent_prompt ---------------------------------------------------------


class TestRenderAgentPrompt:
    """Formatting a prompt template against row data, stringifying non-string values."""

    def test_formats_template_with_row_data(self) -> None:
        result = render_agent_prompt("Discuss {topic}.", {"topic": "space"})
        assert result == "Discuss space."

    def test_stringifies_non_string_values(self) -> None:
        result = render_agent_prompt("Limit is {limit}.", {"limit": 5})
        assert result == "Limit is 5."

    def test_missing_column_raises_contract_error_naming_it(self) -> None:
        with pytest.raises(ContractError) as exc:
            render_agent_prompt("Discuss {topic}.", {"other": "value"})
        assert "topic" in str(exc.value)

    def test_missing_column_error_lists_available_columns(self) -> None:
        with pytest.raises(ContractError) as exc:
            render_agent_prompt("Discuss {topic}.", {"question": "q", "answer": "a"})
        message = str(exc.value)
        assert "answer" in message
        assert "question" in message

    def test_template_with_no_placeholders_ignores_row_data(self) -> None:
        result = render_agent_prompt("Static prompt.", {"anything": "value"})
        assert result == "Static prompt."
