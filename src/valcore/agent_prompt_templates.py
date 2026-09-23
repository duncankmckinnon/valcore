"""Validate and translate agent templates at the Logfire sync boundary.

Agent instructions remain literal text. Input templates use simple Python fields
locally and simple Logfire variables remotely; unsupported syntax is rejected
before either side can change the meaning of a template.
"""

import re
from string import Formatter

from valcore.errors import ConfigError

_COMPOSITION_REFERENCE = re.compile(r"@\{[^{}]*\}@")
_RESERVED_REMOTE_NAMES = frozenset(
    {
        "if",
        "unless",
        "each",
        "with",
        "lookup",
        "log",
        "else",
        "this",
        "true",
        "false",
        "null",
        "undefined",
    }
)


def _input_error(reason: str) -> ConfigError:
    return ConfigError(f"input_template {reason}")


def _valid_field(name: str) -> bool:
    return name.isidentifier() and name not in _RESERVED_REMOTE_NAMES


def local_to_remote_input(text: str) -> str:
    """Convert simple ``{column}`` fields to Logfire ``{{column}}`` variables.

    Python's formatter decodes escaped braces while parsing, so any brace in
    a literal segment means the original text cannot be represented losslessly.
    """
    if not isinstance(text, str):
        raise _input_error("must be a string.")
    if _COMPOSITION_REFERENCE.search(text):
        raise _input_error("cannot contain Logfire composition references (@{...}@).")

    parts: list[str] = []
    canonical_local: list[str] = []
    try:
        for literal, field, format_spec, conversion in Formatter().parse(text):
            if "{" in literal or "}" in literal:
                raise _input_error("cannot contain escaped literal braces.")
            parts.append(literal)
            canonical_local.append(literal)
            if field is None:
                continue
            if literal.endswith("\\"):
                raise _input_error(
                    "has an escaped placeholder; remove the backslash before {column}."
                )
            if not _valid_field(field):
                raise _input_error(f"has an unsupported placeholder {field!r}; use a column name.")
            if conversion is not None or format_spec:
                raise _input_error(
                    f"has formatting on {field!r}; use only simple {{column}} placeholders."
                )
            parts.append("{{" + field + "}}")
            canonical_local.append("{" + field + "}")
    except ValueError as exc:
        raise _input_error(f"has malformed braces: {exc}.") from exc
    if "".join(canonical_local) != text:
        raise _input_error("must use only simple {column} placeholders without format specifiers.")
    return "".join(parts)


def remote_to_local_input(text: str) -> str:
    """Convert simple Logfire ``{{column}}`` variables to local fields.

    All other braces and Logfire expressions are rejected, including blocks,
    helpers, paths, and composition references.
    """
    if not isinstance(text, str):
        raise _input_error("must be a string.")

    parts: list[str] = []
    cursor = 0
    while cursor < len(text):
        opening = text.find("{", cursor)
        closing = text.find("}", cursor)
        if opening < 0 and closing < 0:
            parts.append(text[cursor:])
            break
        if opening < 0 or (closing >= 0 and closing < opening):
            raise _input_error("has an unsupported or unmatched brace.")
        literal = text[cursor:opening]
        if literal.endswith("\\"):
            raise _input_error("has an escaped variable; remove the backslash before {{column}}.")
        parts.append(literal)
        if not text.startswith("{{", opening):
            raise _input_error("must use only {{column}} variables; single braces are unsupported.")
        end = text.find("}}", opening + 2)
        if end < 0:
            raise _input_error("has an unclosed {{column}} variable.")
        name = text[opening + 2 : end]
        if not _valid_field(name):
            raise _input_error(f"has an unsupported variable {name!r}; use a column name.")
        parts.append("{" + name + "}")
        cursor = end + 2
    return "".join(parts)


def validate_instruction_text(value: object) -> str:
    """Return a single literal instruction string suitable for synchronization."""
    if not isinstance(value, str):
        raise ConfigError(
            "instructions must be a single string, including an empty string if desired."
        )
    if "{{" in value or _COMPOSITION_REFERENCE.search(value):
        raise ConfigError(
            "instructions must be literal text without Logfire variables, blocks, or "
            "composition references."
        )
    return value
