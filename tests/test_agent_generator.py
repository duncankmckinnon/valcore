"""Tests for the agent generator.

No network: agent behavior is driven by ``FunctionModel`` returning canned structured
outputs, following the same ``CapturingModel`` pattern as ``test_eval_generator.py``.
"""

import copy

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from valcore.agent_generator import AgentGenerator, GeneratedAgentConfig, generate_agent_draft

from valcore.errors import ConfigError
from valcore.models import FieldType, OutputField

# A structurally- and semantically-valid GeneratedAgentConfig payload for a free-text
# agent: no capabilities, no structured output.
BASE_PAYLOAD: dict = {
    "version_name": "v1",
    "instructions": "You answer billing questions.",
    "prompt_template": "Answer {question}.",
    "required_columns": ["question"],
    "capabilities": [],
    "output_fields": [],
    "rationale": "Plain text suits this.",
}


def _latest_user_prompt(messages: list[ModelMessage]) -> str:
    """Return the most recent user-prompt text carried in ``messages``."""
    for message in reversed(messages):
        for part in getattr(message, "parts", []):
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                return part.content
    return ""


class CapturingModel:
    """A FunctionModel wrapper that records the prompt text of each invocation."""

    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads
        self.prompts: list[str] = []

    @property
    def calls(self) -> int:
        """Number of times the backing model was invoked."""
        return len(self.prompts)

    def model(self) -> FunctionModel:
        """Return a FunctionModel that captures each prompt then emits a payload."""

        def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            payload = self.payloads[min(len(self.prompts), len(self.payloads) - 1)]
            self.prompts.append(_latest_user_prompt(messages))
            tool = info.output_tools[0]
            return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=payload)])

        return FunctionModel(fn)


@pytest.mark.anyio
async def test_free_text_draft() -> None:
    """A free-text draft carries only instructions in its spec, with no deps_mapping."""
    capture = CapturingModel([BASE_PAYLOAD])
    agent = Agent(capture.model(), output_type=GeneratedAgentConfig)

    draft = await generate_agent_draft(
        "Help with billing", model="gateway/anthropic:claude-sonnet-5", agent=agent
    )

    assert draft.spec == {"instructions": "You answer billing questions."}
    assert draft.prompt_template == "Answer {question}."
    assert draft.required_columns == ["question"]
    assert draft.rationale == "Plain text suits this."
    assert draft.deps_mapping == {}
    assert capture.prompts[0] == "Agent description:\nHelp with billing"


@pytest.mark.anyio
async def test_structured_draft() -> None:
    """A structured draft renders capabilities and the full output schema exactly."""
    payload = {
        **copy.deepcopy(BASE_PAYLOAD),
        "capabilities": [{"name": "Planning", "config": {}}],
        "output_fields": [
            {"name": "answer", "type": "str", "description": "The answer."},
            {
                "name": "confidence",
                "type": "int",
                "description": "Confidence 1-5.",
                "minimum": 1,
                "maximum": 5,
            },
            {"name": "score", "type": "float", "description": "A float score."},
            {"name": "escalate", "type": "bool", "description": "Whether to escalate."},
            {
                "name": "topic",
                "type": "enum",
                "description": "The topic.",
                "enum_values": ["billing", "other"],
                "required": False,
            },
        ],
    }
    capture = CapturingModel([payload])
    agent = Agent(capture.model(), output_type=GeneratedAgentConfig)

    draft = await generate_agent_draft(
        "Help with billing", model="gateway/anthropic:claude-sonnet-5", agent=agent
    )

    assert draft.spec["capabilities"] == [{"Planning": {}}]
    assert draft.spec["output_schema"] == {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "description": "The answer."},
            "confidence": {
                "type": "integer",
                "description": "Confidence 1-5.",
                "minimum": 1,
                "maximum": 5,
            },
            "score": {"type": "number", "description": "A float score."},
            "escalate": {"type": "boolean", "description": "Whether to escalate."},
            "topic": {
                "type": "string",
                "description": "The topic.",
                "enum": ["billing", "other"],
            },
        },
        "required": ["answer", "confidence", "score", "escalate"],
    }


def test_output_schema_rejects_duplicate_field_names() -> None:
    """A repeated output field name raises ConfigError naming the field."""
    from valcore.agent_generator import output_schema

    with pytest.raises(ConfigError, match="answer"):
        output_schema(
            [
                OutputField(name="answer", type=FieldType.STR, description="a"),
                OutputField(name="answer", type=FieldType.STR, description="b"),
            ]
        )


def test_build_generator_agent_resolves_a_local_model() -> None:
    """The generator agent is named after its subject and resolves a local CLI model."""
    from valcore.local_cli.bridge_model import CliBridgeModel

    agent = AgentGenerator(model="local/claude").build_generator_agent()

    assert agent.name == "agent_generator"
    assert isinstance(agent.model, CliBridgeModel)
