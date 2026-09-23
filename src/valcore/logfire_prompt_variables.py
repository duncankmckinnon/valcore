"""Read and publish an agent's two prompt texts as Logfire managed variables.

Each operation uses a fresh, local Logfire instance and the single configured
write key. The SDK's latest saved version is the read source; the dedicated
``valcore_sync`` label is used only to create new versions.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import logfire
from logfire.variables.config import LabeledValue, Rollout, VariableConfig, VariablesConfig

from valcore import config
from valcore.errors import ConfigError, SyncConflictError, ValcoreError

TemplateKey = Literal["instructions", "input_template"]
_TEMPLATE_KEYS: tuple[TemplateKey, ...] = ("instructions", "input_template")
_AGENT_ID_PATTERN = re.compile(r"[A-Za-z0-9_]+\Z")
_REQUIRED_SCOPES = "project:read_variables and project:write_variables"
_SYNC_LABEL = "valcore_sync"


@dataclass(frozen=True)
class RemoteTemplate:
    """One variable's latest saved version and unrendered stored text."""

    variable_name: str
    version: int | None
    text: str | None


@dataclass(frozen=True)
class RemoteSnapshot:
    """The two fixed managed variables belonging to an agent."""

    templates: dict[TemplateKey, RemoteTemplate]


def _names(agent_id: str) -> dict[TemplateKey, str]:
    """Build names from a safe immutable agent ID, never from request keys."""
    if not isinstance(agent_id, str) or _AGENT_ID_PATTERN.fullmatch(agent_id) is None:
        raise ValueError("agent_id must contain only letters, digits, and underscores")
    return {key: f"valcore_agent_{agent_id}_{key}" for key in _TEMPLATE_KEYS}


def _snapshot(names: Mapping[TemplateKey, str], remote: VariablesConfig) -> RemoteSnapshot:
    """Select and decode only this agent's latest saved variable versions."""
    templates: dict[TemplateKey, RemoteTemplate] = {}
    for key, name in names.items():
        variable = remote.variables.get(name)
        latest = variable.latest_version if variable is not None else None
        if latest is None:
            templates[key] = RemoteTemplate(name, None, None)
            continue
        try:
            text = json.loads(latest.serialized_value)
        except (TypeError, ValueError):
            raise ConfigError(f"Logfire variable {name} does not contain a JSON string") from None
        if not isinstance(text, str):
            raise ConfigError(f"Logfire variable {name} must contain a string")
        templates[key] = RemoteTemplate(name, latest.version, text)
    return RemoteSnapshot(templates)


def _request_error(exc: Exception) -> ValcoreError:
    """Replace SDK messages that might contain credentials with safe errors."""
    status = getattr(exc, "status_code", None)
    message = str(exc).lower()
    if status in (401, 403) or any(
        token in message for token in ("401", "403", "unauthorized", "forbidden")
    ):
        return ConfigError(f"Logfire variable sync requires an API key with {_REQUIRED_SCOPES}.")
    return ValcoreError("Logfire managed-variable request failed.")


def _pull(instance: logfire.Logfire) -> VariablesConfig:
    """Pull variables while sanitizing SDK errors before they reach an API caller."""
    try:
        return instance.variables_pull_config()
    except Exception as exc:  # noqa: BLE001 - SDK errors may contain the API key
        raise _request_error(exc) from None


class PromptVariableAdapter:
    """An injectable boundary for an agent's two ordinary Logfire variables."""

    def read(self, agent_id: str) -> RemoteSnapshot:
        """Return latest saved raw strings, with absent variables represented by nulls."""
        names = _names(agent_id)
        key = config.resolve_logfire_write_key(config.load_config())
        if not key:
            raise ConfigError(f"Logfire variable sync requires an API key with {_REQUIRED_SCOPES}.")
        try:
            instance = logfire.configure(
                local=True, send_to_logfire=False, console=False, api_key=key
            )
        except Exception as exc:  # noqa: BLE001 - sanitize all SDK failures
            raise _request_error(exc) from None
        try:
            return _snapshot(names, _pull(instance))
        finally:
            instance.shutdown()

    def write(
        self,
        agent_id: str,
        changes: Mapping[TemplateKey, str],
        expected_versions: Mapping[TemplateKey, int | None],
    ) -> RemoteSnapshot:
        """Publish selected texts after checking their latest remote versions.

        The SDK has no atomic compare-and-swap. Callers must re-inspect after a
        partial failure or a concurrent remote write.
        """
        names = _names(agent_id)
        if not set(changes) <= set(_TEMPLATE_KEYS):
            raise ValueError("Unknown prompt template key")
        if set(changes) != set(expected_versions):
            raise ValueError("Expected versions are required for every changed template")
        if any(not isinstance(value, str) for value in changes.values()):
            raise ValueError("Prompt variable values must be strings")

        key = config.resolve_logfire_write_key(config.load_config())
        if not key:
            raise ConfigError(f"Logfire variable sync requires an API key with {_REQUIRED_SCOPES}.")
        try:
            instance = logfire.configure(
                local=True, send_to_logfire=False, console=False, api_key=key
            )
        except Exception as exc:  # noqa: BLE001 - sanitize all SDK failures
            raise _request_error(exc) from None
        try:
            before_config = _pull(instance)
            before = _snapshot(names, before_config)
            for template_key in changes:
                if before.templates[template_key].version != expected_versions[template_key]:
                    raise SyncConflictError(
                        f"Logfire variable {names[template_key]} changed; inspect again."
                    )

            selected: dict[str, VariableConfig] = {}
            expected_after: dict[TemplateKey, int] = {}
            for template_key, text in changes.items():
                old = before.templates[template_key]
                if old.text == text:
                    continue
                existing = before_config.variables.get(old.variable_name)
                if existing is None:
                    variable = VariableConfig(
                        name=old.variable_name,
                        labels={},
                        rollout=Rollout(labels={}),
                        overrides=[],
                        json_schema={"type": "string"},
                    )
                else:
                    if existing.json_schema != {"type": "string"}:
                        raise ConfigError(
                            f"Logfire variable {old.variable_name} must have a string schema"
                        )
                    label = existing.labels.get(_SYNC_LABEL)
                    if label is not None and (
                        not isinstance(label, LabeledValue)
                        or old.version is None
                        or label.version > old.version
                        or (
                            label.version == old.version
                            and label.serialized_value != existing.latest_version.serialized_value
                        )
                    ):
                        raise ConfigError(
                            f"Logfire variable {old.variable_name} has a conflicting sync label"
                        )
                    variable = copy.deepcopy(existing)
                next_version = (old.version or 0) + 1
                variable.labels[_SYNC_LABEL] = LabeledValue(
                    version=next_version,
                    serialized_value=json.dumps(text, ensure_ascii=False),
                )
                selected[old.variable_name] = variable
                expected_after[template_key] = next_version

            if not selected:
                return before
            try:
                instance.variables_push_config(
                    VariablesConfig(variables=selected), mode="merge", yes=True
                )
            except Exception as exc:  # noqa: BLE001 - sanitize all SDK failures
                raise _request_error(exc) from None
            after = _snapshot(names, _pull(instance))
            if any(
                after.templates[template_key].version != version
                or after.templates[template_key].text != changes[template_key]
                for template_key, version in expected_after.items()
            ):
                raise ValcoreError("Logfire variable write could not be verified by read-back.")
            return after
        finally:
            instance.shutdown()
