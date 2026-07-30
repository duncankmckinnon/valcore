"""Tests for the deterministic row-inspection tool registry."""

import inspect

import pytest

from evalcore.errors import ConfigError
from evalcore.tools import (
    TOOL_REGISTRY,
    get_tools,
    json_extract,
    line_count,
    numeric_compare,
    regex_search,
    string_similarity,
    substring_count,
    tool_names,
    word_count,
)


class TestRegexSearch:
    def test_returns_all_non_overlapping_matches(self) -> None:
        assert regex_search("a1b2c3", r"\d") == ["1", "2", "3"]

    def test_returns_groups_when_pattern_has_a_group(self) -> None:
        assert regex_search("k=1;j=2", r"(\w)=(\d)") == [("k", "1"), ("j", "2")]

    def test_no_match_returns_empty(self) -> None:
        assert regex_search("abc", r"\d") == []

    def test_empty_text_returns_empty(self) -> None:
        assert regex_search("", r"\d") == []

    def test_invalid_pattern_returns_empty_not_raises(self) -> None:
        assert regex_search("abc(", "(") == []

    def test_over_long_pattern_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="pattern too long"):
            regex_search("x", "a" * 501)

    def test_pattern_at_max_length_is_accepted(self) -> None:
        # 500 chars of a literal 'a' matches nothing in "x" but must not raise.
        assert regex_search("x", "a" * 500) == []


class TestSubstringCount:
    def test_happy_path(self) -> None:
        assert substring_count("banana", "a") == 3

    def test_non_overlapping(self) -> None:
        assert substring_count("aaaa", "aa") == 2

    def test_case_sensitive_default(self) -> None:
        assert substring_count("AbAb", "a") == 0

    def test_case_insensitive(self) -> None:
        assert substring_count("AbAb", "a", case_sensitive=False) == 2

    def test_empty_needle_returns_zero(self) -> None:
        assert substring_count("abc", "") == 0

    def test_empty_text_returns_zero(self) -> None:
        assert substring_count("", "a") == 0


class TestJsonExtract:
    def test_top_level_key(self) -> None:
        assert json_extract('{"name": "bob"}', "name") == "bob"

    def test_nested_dict_and_list_index(self) -> None:
        data = '{"items": [{"name": "x"}, {"name": "y"}]}'
        assert json_extract(data, "items.0.name") == "x"
        assert json_extract(data, "items.1.name") == "y"

    def test_non_string_value_is_json_serialized(self) -> None:
        assert json_extract('{"n": 5}', "n") == "5"
        assert json_extract('{"flag": true}', "flag") == "true"
        assert json_extract('{"obj": {"a": 1}}', "obj") == '{"a": 1}'

    def test_absent_key_returns_empty(self) -> None:
        assert json_extract('{"a": 1}', "b") == ""

    def test_list_index_out_of_range_returns_empty(self) -> None:
        assert json_extract('{"items": [1, 2]}', "items.5") == ""

    def test_non_numeric_index_into_list_returns_empty(self) -> None:
        assert json_extract('{"items": [1, 2]}', "items.name") == ""

    def test_traversing_into_scalar_returns_empty(self) -> None:
        assert json_extract('{"a": 1}', "a.b") == ""

    def test_malformed_json_returns_empty(self) -> None:
        assert json_extract("not json", "a") == ""
        assert json_extract("{bad", "a") == ""

    def test_plain_string_that_fails_to_parse_returns_empty(self) -> None:
        assert json_extract("hello world", "0") == ""

    def test_empty_path_returns_empty(self) -> None:
        assert json_extract('{"a": 1}', "") == ""

    def test_empty_data_returns_empty(self) -> None:
        assert json_extract("", "a") == ""


class TestStringSimilarity:
    def test_identical_strings_is_one(self) -> None:
        assert string_similarity("hello", "hello") == 1.0

    def test_disjoint_strings_is_zero(self) -> None:
        assert string_similarity("abc", "xyz") == 0.0

    def test_both_empty_is_one(self) -> None:
        assert string_similarity("", "") == 1.0

    def test_partial_overlap_between_zero_and_one(self) -> None:
        ratio = string_similarity("kitten", "sitting")
        assert 0.0 < ratio < 1.0


class TestNumericCompare:
    def test_equal(self) -> None:
        assert numeric_compare(1.0, 1.0) == "equal"

    def test_greater(self) -> None:
        assert numeric_compare(2.0, 1.0) == "greater"

    def test_less(self) -> None:
        assert numeric_compare(1.0, 2.0) == "less"

    def test_within_tolerance_is_equal_positive_diff(self) -> None:
        assert numeric_compare(1.05, 1.0, tolerance=0.1) == "equal"

    def test_within_tolerance_is_equal_negative_diff(self) -> None:
        assert numeric_compare(1.0, 1.05, tolerance=0.1) == "equal"

    def test_exactly_at_tolerance_boundary_is_equal(self) -> None:
        # 0.5 is exactly representable, so the boundary comparison is exact.
        assert numeric_compare(1.5, 1.0, tolerance=0.5) == "equal"

    def test_just_beyond_tolerance_greater(self) -> None:
        assert numeric_compare(1.75, 1.0, tolerance=0.5) == "greater"

    def test_just_beyond_tolerance_less(self) -> None:
        assert numeric_compare(1.0, 1.75, tolerance=0.5) == "less"


class TestWordCount:
    def test_happy_path(self) -> None:
        assert word_count("one two three") == 3

    def test_collapses_repeated_whitespace(self) -> None:
        assert word_count("  a   b\t c\n d ") == 4

    def test_empty_string_is_zero(self) -> None:
        assert word_count("") == 0

    def test_whitespace_only_is_zero(self) -> None:
        assert word_count("   \t\n ") == 0


class TestLineCount:
    def test_single_line(self) -> None:
        assert line_count("one line") == 1

    def test_multiple_lines(self) -> None:
        assert line_count("a\nb\nc") == 3

    def test_trailing_newline_not_counted_as_extra(self) -> None:
        assert line_count("a\nb\n") == 2

    def test_empty_string_is_zero(self) -> None:
        assert line_count("") == 0


class TestGetTools:
    def test_returns_callables_in_order(self) -> None:
        tools = get_tools(["word_count", "line_count"])
        assert tools == [word_count, line_count]

    def test_empty_list_returns_empty(self) -> None:
        assert get_tools([]) == []

    def test_unknown_name_raises_config_error(self) -> None:
        with pytest.raises(ConfigError, match="unknown tool"):
            get_tools(["does_not_exist"])

    def test_unknown_name_among_valid_raises_config_error(self) -> None:
        with pytest.raises(ConfigError):
            get_tools(["word_count", "nope"])


class TestToolNames:
    def test_returns_all_registered_names(self) -> None:
        assert set(tool_names()) == set(TOOL_REGISTRY)

    def test_returns_a_list(self) -> None:
        assert isinstance(tool_names(), list)


class TestRegistryIntegrity:
    def test_every_entry_keyed_by_its_own_name(self) -> None:
        for name, spec in TOOL_REGISTRY.items():
            assert spec.name == name

    @pytest.mark.parametrize("spec", TOOL_REGISTRY.values(), ids=list(TOOL_REGISTRY))
    def test_description_is_non_empty(self, spec: object) -> None:
        assert spec.description.strip()  # type: ignore[attr-defined]

    @pytest.mark.parametrize("spec", TOOL_REGISTRY.values(), ids=list(TOOL_REGISTRY))
    def test_fn_has_docstring(self, spec: object) -> None:
        assert (spec.fn.__doc__ or "").strip()  # type: ignore[attr-defined]

    @pytest.mark.parametrize("spec", TOOL_REGISTRY.values(), ids=list(TOOL_REGISTRY))
    def test_fn_parameters_fully_annotated(self, spec: object) -> None:
        sig = inspect.signature(spec.fn)  # type: ignore[attr-defined]
        for param in sig.parameters.values():
            assert param.annotation is not inspect.Parameter.empty
        assert sig.return_annotation is not inspect.Signature.empty
