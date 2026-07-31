"""Export parity: the exported script must define the same agent that ran in-process.

For each representative ``EvaluatorVersion`` this builds the live agent with
``factory.build_agent`` and the exported agent by ``exec``-ing the rendered
script, then compares model, instructions, output schema, tools, and
capabilities element by element so a failure names the fixture and the diverging
piece. No network happens: the model string is only *resolved* (provider object
constructed), never called, and a dummy gateway key/base URL is injected so that
resolution succeeds offline.
"""

from typing import Any

import pytest
from pydantic_ai import Agent

from valcore import export, factory
from valcore.models import EvaluatorVersion, ScoreKind

# --- private-attribute helpers (pydantic-ai 2.19) -------------------------------
# pydantic-ai does not expose static instructions, the function-tool names, or the
# attached capabilities as public attributes on an un-entered Agent, so these three
# helpers isolate the private reach. If an upgrade moves them, only this block breaks.


def _agent_instructions(agent: Agent[Any, Any]) -> str:
    """Return the static instruction text stored on an un-entered agent."""
    return "\n\n".join(agent._instructions)


def _tool_names(agent: Agent[Any, Any]) -> set[str]:
    """Return the set of function-tool names registered on an agent."""
    return set(agent._function_toolset.tools)


def _capabilities_by_type(agent: Agent[Any, Any]) -> dict[str, Any]:
    """Return the agent's attached capabilities keyed by their class name."""
    return {type(cap).__name__: cap for cap in agent._root_capability.capabilities}


# --- fixtures -------------------------------------------------------------------


def _categorical_with_tools() -> EvaluatorVersion:
    """Categorical verdict evaluator that also exposes row-inspection tools."""
    return EvaluatorVersion(
        evaluator_id="e-cat",
        version_name="Answer Correctness",
        model="gateway/anthropic:claude-sonnet-5",
        instructions="You are a strict grader. Explain your reasoning, then decide.",
        prompt_template="Given {question}, grade {answer}.",
        required_columns=["question", "answer"],
        output_fields=[
            {"name": "reasoning", "type": "str", "description": "Why this verdict."},
            {
                "name": "verdict",
                "type": "enum",
                "description": "The verdict.",
                "enum_values": ["pass", "fail"],
            },
        ],
        score_field="verdict",
        score_kind=ScoreKind.CATEGORICAL,
        score_labels=["pass", "fail"],
        capabilities=[],
        tools=["regex_search", "substring_count"],
    )


def _numeric_with_bounds() -> EvaluatorVersion:
    """Numeric score evaluator with an int score field bounded 1..5."""
    return EvaluatorVersion(
        evaluator_id="e-num",
        version_name="Fluency Score",
        model="gateway/openai:gpt-5",
        instructions="Rate fluency on a 1 to 5 scale, higher is better.",
        prompt_template="Rate the fluency of {text}.",
        required_columns=["text"],
        output_fields=[
            {"name": "rationale", "type": "str", "description": "Justification."},
            {
                "name": "rating",
                "type": "int",
                "description": "Fluency from 1 to 5.",
                "minimum": 1,
                "maximum": 5,
            },
        ],
        score_field="rating",
        score_kind=ScoreKind.NUMERIC,
        score_minimum=1,
        score_maximum=5,
        capabilities=[],
        tools=["numeric_compare", "word_count"],
    )


def _codemode_enabled() -> EvaluatorVersion:
    """Evaluator with the CodeMode capability attached and non-default config."""
    return EvaluatorVersion(
        evaluator_id="e-code",
        version_name="Static Safety",
        model="gateway/anthropic:claude-opus-4-5",
        instructions="Use code to inspect the snippet, then classify it.",
        prompt_template="Analyze this code: {code}.",
        required_columns=["code"],
        output_fields=[
            {
                "name": "verdict",
                "type": "enum",
                "description": "Safety verdict.",
                "enum_values": ["safe", "unsafe"],
            },
        ],
        score_field="verdict",
        score_kind=ScoreKind.CATEGORICAL,
        score_labels=["safe", "unsafe"],
        capabilities=[{"name": "CodeMode", "config": {"max_retries": 2}}],
        tools=["line_count"],
    )


def _optional_fields_and_quoted_instructions() -> EvaluatorVersion:
    """Optional output fields plus instructions containing quotes, tabs, and a backslash."""
    return EvaluatorVersion(
        evaluator_id="e-opt",
        version_name="Reference Match",
        model="gateway/google:gemini-2.5-pro",
        instructions='He said "match it".\nUse a tab:\there.\nEscape a backslash: \\ done.',
        prompt_template="Compare {text} against {reference}.",
        required_columns=["text", "reference"],
        output_fields=[
            {
                "name": "notes",
                "type": "str",
                "description": "Optional free-form notes.",
                "required": False,
            },
            {
                "name": "confidence",
                "type": "float",
                "description": "Optional confidence 0..1.",
                "minimum": 0.0,
                "maximum": 1.0,
                "required": False,
            },
            {
                "name": "label",
                "type": "enum",
                "description": "Whether it matches.",
                "enum_values": ["yes", "no"],
            },
        ],
        score_field="label",
        score_kind=ScoreKind.CATEGORICAL,
        score_labels=["yes", "no"],
        capabilities=[],
        tools=[],
    )


FIXTURES: dict[str, EvaluatorVersion] = {
    "categorical_with_tools": _categorical_with_tools(),
    "numeric_with_bounds": _numeric_with_bounds(),
    "codemode_enabled": _codemode_enabled(),
    "optional_fields_quoted_instructions": _optional_fields_and_quoted_instructions(),
}

VERSIONS = list(FIXTURES.items())
IDS = list(FIXTURES)


@pytest.fixture(autouse=True)
def _gateway_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a dummy gateway key + base URL so the exported agent resolves offline.

    The exported script constructs its ``Agent`` without ``defer_model_check``, so
    pydantic-ai instantiates the gateway provider at build time. That construction
    only needs the two env vars present; it performs no network I/O.
    """
    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_API_KEY", "dummy-test-key")
    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_BASE_URL", "https://gateway.invalid")


def _build_pair(
    version: EvaluatorVersion,
) -> tuple[Agent[Any, Any], dict[str, Any], Agent[Any, Any]]:
    """Build the live agent, exec the exported script, and return the exported agent."""
    live = factory.build_agent(version)
    ns: dict[str, Any] = {}
    exec(compile(export.render_script(version), "<export>", "exec"), ns)  # noqa: S102 - exercising the exported script is the whole point
    exported = ns["_agent"]()
    return live, ns, exported


def _properties(schema: dict[str, Any]) -> dict[str, Any]:
    """Return the ``properties`` map of a JSON schema."""
    return schema.get("properties", {})


def _enum_members(prop: dict[str, Any]) -> tuple[str, ...] | None:
    """Return the enum members of a JSON-schema property, if any, else None."""
    if "enum" in prop:
        return tuple(prop["enum"])
    for sub in prop.get("anyOf", []):
        if "enum" in sub:
            return tuple(sub["enum"])
    return None


@pytest.mark.parametrize("name, version", VERSIONS, ids=IDS)
def test_model_string_parity(name: str, version: EvaluatorVersion) -> None:
    """Both agents target ``version.model``."""
    live, ns, exported = _build_pair(version)

    assert live.model == version.model, (
        f"[{name}] live agent model {live.model!r} != {version.model!r}"
    )
    assert ns["MODEL"] == version.model, (
        f"[{name}] exported MODEL constant {ns['MODEL']!r} != {version.model!r}"
    )
    # The exported agent resolves the string to a provider model; its model_name is
    # the segment after the gateway route and must match the version's model.
    expected_name = version.model.split(":", 1)[1]
    assert exported.model.model_name == expected_name, (
        f"[{name}] exported agent resolved to model_name {exported.model.model_name!r}, "
        f"expected {expected_name!r}"
    )


@pytest.mark.parametrize("name, version", VERSIONS, ids=IDS)
def test_instructions_parity(name: str, version: EvaluatorVersion) -> None:
    """Instructions are byte-for-byte identical to the version on both sides."""
    live, _ns, exported = _build_pair(version)

    live_instr = _agent_instructions(live)
    exported_instr = _agent_instructions(exported)
    assert live_instr == version.instructions, (
        f"[{name}] live instructions diverge from version:\n"
        f"  live:    {live_instr!r}\n  version: {version.instructions!r}"
    )
    assert exported_instr == version.instructions, (
        f"[{name}] exported instructions diverge from version:\n"
        f"  exported: {exported_instr!r}\n  version:  {version.instructions!r}"
    )


@pytest.mark.parametrize("name, version", VERSIONS, ids=IDS)
def test_output_schema_parity(name: str, version: EvaluatorVersion) -> None:
    """The live and exported output models agree field by field, modulo schema title."""
    live, ns, _exported = _build_pair(version)

    live_schema = live.output_type.model_json_schema()
    exported_schema = ns["OutputModel"].model_json_schema()
    live_props = _properties(live_schema)
    exported_props = _properties(exported_schema)

    # Field names and their order.
    expected_order = [f["name"] for f in version.output_fields]
    assert list(live_props) == expected_order, (
        f"[{name}] live field order {list(live_props)} != declared {expected_order}"
    )
    assert list(exported_props) == list(live_props), (
        f"[{name}] exported field order {list(exported_props)} != live {list(live_props)}"
    )

    # Required-ness (the ``required`` array), whole and per fixture.
    assert live_schema.get("required", []) == exported_schema.get("required", []), (
        f"[{name}] required fields differ: live {live_schema.get('required', [])} "
        f"vs exported {exported_schema.get('required', [])}"
    )

    # Descriptions, enum members, and the full per-field property (type, bounds,
    # nullability, default) each compared individually so a failure names the field.
    for field in list(live_props):
        lp = live_props[field]
        ep = exported_props[field]
        assert lp.get("description") == ep.get("description"), (
            f"[{name}.{field}] description diverged: "
            f"live {lp.get('description')!r} vs exported {ep.get('description')!r}"
        )
        assert _enum_members(lp) == _enum_members(ep), (
            f"[{name}.{field}] enum members diverged: "
            f"live {_enum_members(lp)} vs exported {_enum_members(ep)}"
        )
        assert lp == ep, (
            f"[{name}.{field}] property schema diverged:\n  live:     {lp}\n  exported: {ep}"
        )


@pytest.mark.parametrize("name, version", VERSIONS, ids=IDS)
def test_tools_parity(name: str, version: EvaluatorVersion) -> None:
    """The set of tool names on each agent matches ``version.tools``."""
    live, _ns, exported = _build_pair(version)

    expected = set(version.tools)
    assert _tool_names(live) == expected, (
        f"[{name}] live tools {_tool_names(live)} != declared {expected}"
    )
    assert _tool_names(exported) == expected, (
        f"[{name}] exported tools {_tool_names(exported)} != declared {expected}"
    )


@pytest.mark.parametrize("name, version", VERSIONS, ids=IDS)
def test_capabilities_parity(name: str, version: EvaluatorVersion) -> None:
    """The same capability types with the same config are attached on both sides."""
    live, _ns, exported = _build_pair(version)

    live_caps = _capabilities_by_type(live)
    exported_caps = _capabilities_by_type(exported)

    assert set(live_caps) == set(exported_caps), (
        f"[{name}] capability types differ: live {sorted(live_caps)} "
        f"vs exported {sorted(exported_caps)}"
    )

    for cap_type in live_caps:
        assert live_caps[cap_type] == exported_caps[cap_type], (
            f"[{name}] capability {cap_type} config diverged:\n"
            f"  live:     {live_caps[cap_type]!r}\n  exported: {exported_caps[cap_type]!r}"
        )

    # Every capability the version declared is actually attached on both sides.
    for spec in version.capabilities:
        cap_type = spec["name"]
        assert cap_type in live_caps, (
            f"[{name}] declared capability {cap_type} missing from live agent"
        )
        assert cap_type in exported_caps, (
            f"[{name}] declared capability {cap_type} missing from exported agent"
        )
