"""Invocation bindings must not reinterpret dataset names as function arguments."""

import pytest

from valcore.errors import ContractError
from valcore.invocation import bind_invocation, check_mapping
from valcore.models import EvaluatorVersion


def version(**kwargs):
    return EvaluatorVersion(input_contract="invocation", required_columns=["tenant"], **kwargs)


def test_mapping_preserves_null_output_and_metadata_names():
    metadata, invocation = bind_invocation(
        version(),
        {"q": [1], "a": None, "header": "acme"},
        {"input": "q", "output": "a", "metadata": {"tenant": "header"}},
        None,
    )
    assert invocation == {"input": [1], "output": None}
    assert metadata == {"tenant": "acme"}


def test_output_only_recording_does_not_require_dataset_io_columns():
    metadata, invocation = bind_invocation(
        version(input_mode="output"), {"tenant": "acme"}, {}, {"output": False}
    )
    assert invocation == {"output": False}
    assert metadata == {"tenant": "acme"}


def test_mapping_requires_output_for_plain_dataset():
    with pytest.raises(ContractError, match="output"):
        check_mapping(version(), ["tenant"], {}, has_invocation=False)


def test_missing_input_is_not_guessed_from_dataset():
    with pytest.raises(ContractError, match="input"):
        bind_invocation(
            version(), {"input": "not the call", "tenant": "a"}, {}, {"output": "answer"}
        )


def test_metadata_is_not_satisfied_by_agent_output():
    with pytest.raises(ContractError, match="tenant"):
        bind_invocation(version(input_mode="output"), {}, {}, {"output": {"tenant": "fake"}})
