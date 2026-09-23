"""Tests for the agent prompt-template sync boundary.

Pins ``agent_prompt_templates``: the pure conversion of valcore's ``{column}`` input-template
placeholders to and from Logfire's ``{{column}}`` convention, and validation of the single literal
instruction string. Conversion must round-trip exactly, and every unsupported form must raise a
``ConfigError`` naming the offending field rather than being flattened or partially rendered.
"""

import pytest
from pydantic_handlebars import render

from valcore.agent_prompt_templates import (
    local_to_remote_input,
    remote_to_local_input,
    validate_instruction_text,
)
from valcore.agent_spec import render_agent_prompt
from valcore.errors import ConfigError

# --- local -> remote -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("local", "remote"),
    [
        ("Question: {question}", "Question: {{question}}"),
        ("{a} and {b}, then {a}", "{{a}} and {{b}}, then {{a}}"),
        ("{_private} {col2}", "{{_private}} {{col2}}"),
        ("no placeholders at all", "no placeholders at all"),
        ("line one\n{x}\n\tline three  ", "line one\n{{x}}\n\tline three  "),
        ("", ""),
        ("Café {名前} — 🚀 {émoji}", "Café {{名前}} — 🚀 {{émoji}}"),
        ("{a}{b}", "{{a}}{{b}}"),
    ],
)
def test_local_to_remote_converts_placeholders(local: str, remote: str) -> None:
    assert local_to_remote_input(local) == remote


@pytest.mark.parametrize(
    "remote",
    [
        "Question: {{question}}",
        "{{a}} and {{b}}, then {{a}}",
        "plain text",
        "",
        "Café {{名前}} — 🚀",
        "multi\nline {{x}}\n",
    ],
)
def test_remote_to_local_to_remote_round_trips_exactly(remote: str) -> None:
    assert local_to_remote_input(remote_to_local_input(remote)) == remote


@pytest.mark.parametrize(
    "local",
    [
        "Question: {question}",
        "{a} and {b}, then {a}",
        "",
        "Café {名前} — 🚀 {émoji}",
        "  leading and trailing whitespace {x}  \n",
        "no placeholders",
    ],
)
def test_local_to_remote_to_local_round_trips_exactly(local: str) -> None:
    assert remote_to_local_input(local_to_remote_input(local)) == local


def test_empty_input_template_round_trips_unchanged() -> None:
    assert local_to_remote_input("") == ""
    assert remote_to_local_input("") == ""


def test_converted_input_renders_the_same_on_both_sides() -> None:
    local = "Hello {name}, meet {friend}!"
    values = {"name": "Alice", "friend": "Bob"}
    assert render_agent_prompt(local, values) == render(local_to_remote_input(local), values)


def test_escaped_expression_would_change_rendered_meaning() -> None:
    local = r"\{name}"
    values = {"name": "Alice"}
    assert render_agent_prompt(local, values) == r"\Alice"
    assert render(r"\{{name}}", values) == "{{name}}"
    with pytest.raises(ConfigError, match="input_template.*escaped"):
        local_to_remote_input(local)
    with pytest.raises(ConfigError, match="input_template.*escaped"):
        remote_to_local_input(r"\{{name}}")


@pytest.mark.parametrize(
    "name",
    ["if", "unless", "each", "with", "lookup", "log", "true", "false", "null", "undefined"],
)
def test_reserved_handlebars_names_are_rejected_on_both_sides(name: str) -> None:
    with pytest.raises(ConfigError, match="input_template"):
        local_to_remote_input("{" + name + "}")
    with pytest.raises(ConfigError, match="input_template"):
        remote_to_local_input("{{" + name + "}}")


@pytest.mark.parametrize("name", ["if", "true"])
def test_reserved_names_render_differently_from_columns(name: str) -> None:
    values = {name: "Alice"}
    assert render_agent_prompt("{" + name + "}", values) == "Alice"
    assert render("{{" + name + "}}", values) != "Alice"


# --- local -> remote rejections --------------------------------------------------------------


@pytest.mark.parametrize(
    "local",
    [
        "{x!r}",  # conversion
        "{x!s}",
        "{x:>10}",  # format spec
        "{x:.2f}",
        "{}",  # auto-numbered positional
        "{0}",  # indexed positional
        "{a.b}",  # attribute access
        "{a[0]}",  # indexing
        "{1abc}",  # invalid identifier
        "{a-b}",  # invalid identifier
        "{ a }",  # whitespace in name
        "{a b}",
        "{{literal}}",  # literal brace escape has no remote representation
        "{{",
        "}}",
        "cost is 5{{%}}",
        "{unclosed",  # malformed
        "stray } brace",
    ],
)
def test_local_to_remote_rejects_unsupported_forms(local: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        local_to_remote_input(local)
    assert "input_template" in str(excinfo.value)


def test_local_to_remote_rejection_message_is_actionable() -> None:
    with pytest.raises(ConfigError) as excinfo:
        local_to_remote_input("{score:.2f}")
    message = str(excinfo.value)
    assert "input_template" in message
    assert message.strip() != "input_template"


def test_local_to_remote_does_not_partially_convert_on_error() -> None:
    with pytest.raises(ConfigError):
        local_to_remote_input("{ok} then {bad!r}")


# --- remote -> local rejections --------------------------------------------------------------


@pytest.mark.parametrize(
    "remote",
    [
        "{{a.b}}",  # dotted path
        "{{a.b.c}}",
        "{{a[0]}}",  # indexing
        "{{#if a}}yes{{/if}}",  # conditional block
        "{{#each items}}{{this}}{{/each}}",  # loop block
        "{{#unless a}}no{{/unless}}",
        "{{^a}}inverted{{/a}}",
        "{{else}}",
        "{{> partial}}",  # partial / composition
        "{{! a comment }}",
        "{{upper a}}",  # helper call
        "{{a | upper}}",
        "{{{a}}}",  # unescaped triple-stash
        "{{}}",  # empty placeholder
        "{{1abc}}",  # invalid name
        "{{a-b}}",
        "{{a",  # unbalanced
        "a}}",
        "@{prompt_ref}@",  # composition reference
        "Intro @{shared}@ then {{a}}",
        "{literal}",  # single braces would become a placeholder locally
        "{a} {{b}}",
    ],
)
def test_remote_to_local_rejects_unsupported_forms(remote: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        remote_to_local_input(remote)
    assert "input_template" in str(excinfo.value)


# --- validate_instruction_text ---------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "You are a careful judge.",
        "",
        "Multi\nline\n  instructions",
        "Café — 名前 🚀",
        'Return JSON like {"label": "x"} exactly.',  # single braces are literal text
        "Use {column} literally.",
        "email me @ foo@bar.com",  # lone @ is fine
    ],
)
def test_validate_instruction_text_returns_string_unchanged(value: str) -> None:
    assert validate_instruction_text(value) == value


@pytest.mark.parametrize("value", [None, ["a", "b"], [], ("a",), 3, {"a": 1}, b"bytes"])
def test_validate_instruction_text_rejects_non_string(value: object) -> None:
    with pytest.raises(ConfigError) as excinfo:
        validate_instruction_text(value)
    assert "instructions" in str(excinfo.value)


@pytest.mark.parametrize(
    "value",
    [
        "Judge {{question}}",  # variable interpolation
        "{{#if strict}}Be strict.{{/if}}",  # conditional
        "{{#each rules}}{{this}}{{/each}}",  # loop
        "{{> shared_rules}}",  # partial
        "Follow @{shared_rules}@ closely.",  # composition
        "Prefix {{",  # any template opener
    ],
)
def test_validate_instruction_text_rejects_logfire_expressions(value: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        validate_instruction_text(value)
    assert "instructions" in str(excinfo.value)
