"""Deterministic row-inspection tools an evaluator agent can call."""

import difflib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from valcore.errors import ConfigError

_MAX_PATTERN_LEN = 500


@dataclass(frozen=True)
class ToolSpec:
    """A named, described row-inspection tool exposed to an evaluator agent."""

    name: str
    description: str
    fn: Callable[..., Any]


def regex_search(text: str, pattern: str) -> list[str]:
    """Return all non-overlapping matches of a regex pattern in text.

    Invalid patterns yield an empty list; patterns over 500 characters are rejected.
    """
    if len(pattern) > _MAX_PATTERN_LEN:
        raise ValueError(f"pattern too long: {len(pattern)} chars (max {_MAX_PATTERN_LEN})")
    try:
        compiled = re.compile(pattern)
    except re.error:
        return []
    return compiled.findall(text)


def substring_count(text: str, needle: str, case_sensitive: bool = True) -> int:
    """Count non-overlapping occurrences of a substring in text."""
    if not needle:
        return 0
    if case_sensitive:
        return text.count(needle)
    return text.lower().count(needle.lower())


def json_extract(data: str, path: str) -> str:
    """Extract a value from a JSON string via a dotted path with numeric list indices.

    Returns "" when the data is not valid JSON or the path is absent.
    """
    try:
        current: Any = json.loads(data)
    except (json.JSONDecodeError, ValueError):
        return ""
    if not path:
        return ""
    for segment in path.split("."):
        if isinstance(current, dict):
            if segment not in current:
                return ""
            current = current[segment]
        elif isinstance(current, list):
            if not (segment.isdigit() and int(segment) < len(current)):
                return ""
            current = current[int(segment)]
        else:
            return ""
    if isinstance(current, str):
        return current
    return json.dumps(current)


def string_similarity(a: str, b: str) -> float:
    """Return the difflib SequenceMatcher ratio between two strings, 0.0-1.0."""
    return difflib.SequenceMatcher(None, a, b).ratio()


def numeric_compare(a: float, b: float, tolerance: float = 0.0) -> str:
    """Compare two numbers, treating a difference within tolerance as equal.

    Returns "equal", "greater", or "less".
    """
    if abs(a - b) <= tolerance:
        return "equal"
    return "greater" if a > b else "less"


def word_count(text: str) -> int:
    """Count whitespace-separated words in text."""
    return len(text.split())


def line_count(text: str) -> int:
    """Count lines in text."""
    if not text:
        return 0
    return len(text.splitlines())


TOOL_REGISTRY: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            "regex_search",
            "Return all non-overlapping regex matches in a text.",
            regex_search,
        ),
        ToolSpec(
            "substring_count",
            "Count occurrences of a substring, optionally case-insensitively.",
            substring_count,
        ),
        ToolSpec(
            "json_extract",
            "Extract a value from a JSON string by dotted path (e.g. items.0.name).",
            json_extract,
        ),
        ToolSpec(
            "string_similarity",
            "Compute the similarity ratio between two strings, from 0.0 to 1.0.",
            string_similarity,
        ),
        ToolSpec(
            "numeric_compare",
            "Compare two numbers within a tolerance, returning equal, greater, or less.",
            numeric_compare,
        ),
        ToolSpec(
            "word_count",
            "Count the number of words in a text.",
            word_count,
        ),
        ToolSpec(
            "line_count",
            "Count the number of lines in a text.",
            line_count,
        ),
    )
}


def get_tools(names: Sequence[str]) -> list[Callable[..., Any]]:
    """Return the callables for the named tools, raising ConfigError on an unknown name."""
    tools: list[Callable[..., Any]] = []
    for name in names:
        spec = TOOL_REGISTRY.get(name)
        if spec is None:
            raise ConfigError(f"unknown tool: {name!r}")
        tools.append(spec.fn)
    return tools


def tool_names() -> list[str]:
    """Return the names of all registered tools."""
    return list(TOOL_REGISTRY)
