"""Explicit, versioned synchronization of agent text templates with Logfire.

The service owns comparison and revision checks. The store owns transactional
local mutations, while the adapter is the only boundary that contacts Logfire.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from valcore.agent_prompt_templates import (
    local_to_remote_input,
    remote_to_local_input,
    validate_instruction_text,
)
from valcore.errors import ConfigError, SyncConflictError, ValcoreError
from valcore.logfire_prompt_variables import PromptVariableAdapter, RemoteSnapshot
from valcore.models import AgentPromptSyncLink, AgentVersion, validate_agent_version
from valcore.store import Store

TemplateKey = Literal["instructions", "input_template"]
KEYS: tuple[TemplateKey, ...] = ("instructions", "input_template")
State = Literal[
    "in_sync", "local_changed", "remote_changed", "conflict", "remote_missing", "unsupported"
]


@dataclass(frozen=True)
class TemplateStatus:
    """A three-way comparison for one text template."""

    variable_name: str
    state: State
    local_text: str | None
    base_text: str | None
    remote_text: str | None
    remote_version: int | None
    base_remote_version: int | None
    error: str | None = None


@dataclass(frozen=True)
class SyncStatus:
    """Browser-safe sync status and revision for an agent."""

    linked: bool
    local_version_id: str | None
    revision: str
    error: str | None
    templates: dict[TemplateKey, TemplateStatus]


@dataclass(frozen=True)
class _Inspection:
    status: SyncStatus
    link: AgentPromptSyncLink | None
    version: AgentVersion | None
    remote: RemoteSnapshot | None
    fingerprint: str | None


def _name(agent_id: str, key: TemplateKey) -> str:
    return f"valcore_agent_{agent_id}_{key}"


def _local_text(version: AgentVersion | None, key: TemplateKey) -> object:
    if version is None:
        return None
    return version.spec.get("instructions") if key == "instructions" else version.prompt_template


def _local_texts(version: AgentVersion) -> dict[str, str]:
    return {key: _local_text(version, key) for key in KEYS}  # type: ignore[return-value]


def _version_fields(source: AgentVersion, texts: dict[str, str]) -> dict[str, object]:
    return {
        "version_name": f"{source.version_name} (Logfire sync {uuid4().hex[:8]})",
        "notes": source.notes,
        "frozen": False,
        "model": source.model,
        "spec": {**source.spec, "instructions": texts["instructions"]},
        "prompt_template": texts["input_template"],
        "required_columns": list(source.required_columns),
        "deps_mapping": dict(source.deps_mapping),
    }


def _source_fields(source: AgentVersion) -> dict[str, object]:
    """Capture every source field copied into a pulled or remote-first version."""
    return {
        "version_name": source.version_name,
        "notes": source.notes,
        "frozen": source.frozen,
        "model": source.model,
        "spec": deepcopy(source.spec),
        "prompt_template": source.prompt_template,
        "required_columns": list(source.required_columns),
        "deps_mapping": deepcopy(source.deps_mapping),
    }


def _validated_remote(key: TemplateKey, raw: str, version: AgentVersion | None) -> str:
    if key == "instructions":
        return validate_instruction_text(raw)
    local = remote_to_local_input(raw)
    if local_to_remote_input(local) != raw:
        raise ConfigError("input_template cannot round-trip exactly through valcore format.")
    if version is not None:
        fields = _version_fields(
            version,
            {"instructions": str(_local_text(version, "instructions")), "input_template": local},
        )
        validate_agent_version(AgentVersion(agent_id=version.agent_id, **fields))
    return local


def _validate_local(key: TemplateKey, value: object) -> str:
    if key == "instructions":
        return validate_instruction_text(value)
    remote = local_to_remote_input(value)  # type: ignore[arg-type]
    if remote_to_local_input(remote) != value:
        raise ConfigError("input_template cannot round-trip exactly through Logfire format.")
    return value  # type: ignore[return-value]


def _state(local: str, base: str | None, remote: str | None) -> State:
    if remote is None:
        return "remote_missing"
    if local == remote:
        return "in_sync"
    if base is None:
        return "conflict"
    local_changed = local != base
    remote_changed = remote != base
    if local_changed and remote_changed:
        return "conflict"
    if local_changed:
        return "local_changed"
    if remote_changed:
        return "remote_changed"
    return "conflict"


class AgentPromptSync:
    """Coordinate explicit two-template sync through a store and variable adapter."""

    def __init__(
        self,
        store: Store,
        adapter: PromptVariableAdapter,
        key_fingerprint: Callable[[], str | None],
    ) -> None:
        self.store = store
        self.adapter = adapter
        self.key_fingerprint = key_fingerprint

    def _inspect(self, agent_id: str, *, read_remote: bool = True) -> _Inspection:
        agent = self.store.get_agent(agent_id)
        link = self.store.get_agent_prompt_sync_link(agent_id)
        version = (
            self.store.get_agent_version(agent.active_version_id)
            if agent.active_version_id
            else None
        )
        fingerprint = self.key_fingerprint()
        error: str | None = None
        remote: RemoteSnapshot | None = None
        if fingerprint is None:
            error = "Configure a Logfire API key with read and write variable scopes."
        elif link is not None and fingerprint != link.key_fingerprint:
            error = "The configured Logfire key changed; unlink before linking the new project."
        elif read_remote:
            remote = self.adapter.read(agent_id, expected_fingerprint=fingerprint)

        templates: dict[TemplateKey, TemplateStatus] = {}
        for key in KEYS:
            name = _name(agent_id, key)
            base = getattr(link, f"{key}_base_text") if link else None
            base_version = getattr(link, f"{key}_remote_version") if link else None
            raw_local = _local_text(version, key)
            local = raw_local if isinstance(raw_local, str) else None
            item = remote.templates[key] if remote is not None else None
            raw_remote = item.text if item else None
            remote_text: str | None = None
            invalid = version is None
            template_error: str | None = (
                "Agent has no active version to sync." if version is None else None
            )
            if version is not None:
                try:
                    _validate_local(key, raw_local)
                except ConfigError as exc:
                    invalid = True
                    template_error = str(exc)
            if raw_remote is not None:
                try:
                    remote_text = _validated_remote(key, raw_remote, version)
                except ConfigError as exc:
                    invalid = True
                    template_error = str(exc)
            state: State = (
                "unsupported" if invalid or error else _state(local or "", base, remote_text)
            )
            templates[key] = TemplateStatus(
                variable_name=name,
                state=state,
                local_text=local,
                base_text=base,
                remote_text=remote_text,
                remote_version=item.version if item else None,
                base_remote_version=base_version,
                error=template_error,
            )

        cursor = (
            {
                "id": link.id,
                "generation": link.generation,
                "agent_version_id": link.agent_version_id,
                "key_fingerprint": link.key_fingerprint,
                "templates": {
                    key: [
                        getattr(link, f"{key}_variable_name"),
                        getattr(link, f"{key}_remote_version"),
                        getattr(link, f"{key}_base_text"),
                    ]
                    for key in KEYS
                },
            }
            if link
            else None
        )
        revision_data: dict[str, object] = {
            "local_version_id": agent.active_version_id,
            "local_texts": {key: _local_text(version, key) for key in KEYS},
            "configured_key_fingerprint": fingerprint,
            "cursor": cursor,
        }
        if remote is not None:
            revision_data["remote"] = {
                key: [remote.templates[key].version, remote.templates[key].text] for key in KEYS
            }
        revision = hashlib.sha256(
            json.dumps(revision_data, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        status = SyncStatus(link is not None, agent.active_version_id, revision, error, templates)
        return _Inspection(status, link, version, remote, fingerprint)

    def inspect(self, agent_id: str) -> SyncStatus:
        """Read local and remote heads without changing either side."""
        return self._inspect(agent_id).status

    def _checked(
        self, agent_id: str, expected_revision: str, *, unlink: bool = False
    ) -> _Inspection:
        inspected = self._inspect(agent_id)
        if inspected.status.error and not unlink:
            if inspected.fingerprint is None:
                raise ConfigError(inspected.status.error)
            raise ValcoreError(inspected.status.error)
        if inspected.status.revision != expected_revision:
            raise SyncConflictError("Prompt sync state changed; inspect sync again.")
        return inspected

    @staticmethod
    def _require_link(inspected: _Inspection) -> AgentPromptSyncLink:
        if inspected.link is None:
            raise ValcoreError("Agent is not linked; link it before syncing.")
        return inspected.link

    @staticmethod
    def _require_version(inspected: _Inspection) -> AgentVersion:
        if inspected.version is None:
            raise ValcoreError("Agent has no active version to sync.")
        return inspected.version

    @staticmethod
    def _require_valid_local(version: AgentVersion) -> None:
        """All cursor writes require both local texts to have supported forms."""
        for key in KEYS:
            _validate_local(key, _local_text(version, key))

    @staticmethod
    def _selected(
        inspected: _Inspection, fields: Sequence[str] | None, eligible: set[State]
    ) -> list[TemplateKey]:
        records = inspected.status.templates
        if fields is None:
            return [
                key
                for key in KEYS
                if records[key].state in eligible
                or (
                    records[key].state == "in_sync"
                    and AgentPromptSync._needs_reconcile(records[key])
                )
            ]
        selected: list[TemplateKey] = []
        for field in fields:
            if field not in KEYS:
                raise ConfigError(f"Unknown sync field {field!r}.")
            record = records[field]
            if record.state not in eligible and not (
                record.state == "in_sync" and AgentPromptSync._needs_reconcile(record)
            ):
                raise ConfigError(
                    f"{field} is {record.state} and is not eligible for this operation."
                    + (f" {record.error}" if record.error else "")
                )
            if field not in selected:
                selected.append(field)  # type: ignore[arg-type]
        return selected

    @staticmethod
    def _needs_reconcile(record: TemplateStatus) -> bool:
        return record.local_text == record.remote_text and (
            record.base_text != record.local_text
            or record.base_remote_version != record.remote_version
        )

    @staticmethod
    def _updates(
        inspected: _Inspection, fields: Sequence[TemplateKey]
    ) -> dict[str, dict[str, object]]:
        return {
            key: {
                "base_text": inspected.status.templates[key].local_text,
                "remote_version": inspected.status.templates[key].remote_version,
            }
            for key in fields
        }

    def link(self, agent_id: str, initial: str, expected_revision: str) -> SyncStatus:
        """Create a cursor, choosing the local or remote text for first sync."""
        inspected = self._checked(agent_id, expected_revision)
        if inspected.link is not None:
            raise ValcoreError("Agent is already linked.")
        version = self._require_version(inspected)
        if initial not in ("local", "remote"):
            raise ConfigError("initial must be 'local' or 'remote'.")
        local = {key: _validate_local(key, _local_text(version, key)) for key in KEYS}
        records = inspected.status.templates
        for key in KEYS:
            if records[key].state == "unsupported":
                raise ConfigError(
                    f"{key} cannot be synced: {records[key].error or 'fix the template first.'}"
                )
        if initial == "remote":
            for key in KEYS:
                if records[key].state == "remote_missing":
                    raise ConfigError(f"{key} remote variable is missing; use initial='local'.")
            chosen = {key: records[key].remote_text for key in KEYS}
            assert all(isinstance(value, str) for value in chosen.values())
            texts = chosen  # type: ignore[assignment]
            fields = _version_fields(version, texts)
            validate_agent_version(AgentVersion(agent_id=agent_id, **fields))
            initial_fields = fields if texts != local else None
            snapshot = inspected.remote
        else:
            snapshot = inspected.remote
            assert snapshot is not None
            changes = {
                key: local_to_remote_input(local[key]) if key == "input_template" else local[key]
                for key in KEYS
                if snapshot.templates[key].text
                != (local_to_remote_input(local[key]) if key == "input_template" else local[key])
            }
            if changes:
                snapshot = self.adapter.write(
                    agent_id,
                    changes,
                    {
                        key: (
                            inspected.remote.templates[key].version,
                            inspected.remote.templates[key].text,
                        )
                        for key in changes
                    },
                    expected_fingerprint=inspected.fingerprint,
                )
                if any(snapshot.templates[key].text != text for key, text in changes.items()):
                    raise SyncConflictError(
                        "Logfire variable read-back changed; inspect sync again."
                    )
            texts = local
            initial_fields = None
        assert snapshot is not None
        self.store.create_agent_prompt_sync_link(
            agent_id,
            key_fingerprint=inspected.fingerprint,
            instructions_variable_name=_name(agent_id, "instructions"),
            input_template_variable_name=_name(agent_id, "input_template"),
            instructions_remote_version=snapshot.templates["instructions"].version,
            input_template_remote_version=snapshot.templates["input_template"].version,
            instructions_base_text=texts["instructions"],
            input_template_base_text=texts["input_template"],
            expected_active_version_id=version.id,
            expected_local_texts=local,
            initial_version_fields=initial_fields,
            expected_source_fields=_source_fields(version) if initial_fields else None,
        )
        return self.inspect(agent_id)

    def _pull(
        self, agent_id: str, inspected: _Inspection, selected: list[TemplateKey]
    ) -> SyncStatus:
        link = self._require_link(inspected)
        version = self._require_version(inspected)
        if not selected:
            return inspected.status
        records = inspected.status.templates
        texts = _local_texts(version)
        for key in selected:
            remote_text = records[key].remote_text
            assert remote_text is not None
            texts[key] = remote_text
        updates = {
            key: {"base_text": texts[key], "remote_version": records[key].remote_version}
            for key in selected
        }
        if texts == _local_texts(version):
            self.store.advance_agent_prompt_sync_link(
                agent_id,
                expected_link_id=link.id,
                expected_generation=link.generation,
                field_updates=updates,
                expected_active_version_id=version.id,
                expected_local_texts=_local_texts(version),
            )
        else:
            fields = _version_fields(version, texts)
            validate_agent_version(AgentVersion(agent_id=agent_id, **fields))
            self.store.create_agent_version_and_advance_prompt_sync_link(
                agent_id,
                expected_link_id=link.id,
                expected_active_version_id=version.id,
                expected_local_texts=_local_texts(version),
                expected_generation=link.generation,
                version_fields=fields,
                field_updates=updates,
                expected_source_fields=_source_fields(version),
            )
        return self.inspect(agent_id)

    def pull(
        self, agent_id: str, fields: Sequence[str] | None, expected_revision: str
    ) -> SyncStatus:
        """Pull remote-only changes into a copied active agent version."""
        inspected = self._checked(agent_id, expected_revision)
        self._require_link(inspected)
        self._require_valid_local(self._require_version(inspected))
        selected = self._selected(inspected, fields, {"remote_changed"})
        return self._pull(agent_id, inspected, selected)

    def _push(
        self, agent_id: str, inspected: _Inspection, selected: list[TemplateKey]
    ) -> SyncStatus:
        link = self._require_link(inspected)
        version = self._require_version(inspected)
        if not selected:
            return inspected.status
        assert inspected.remote is not None
        local = _local_texts(version)
        records = inspected.status.templates
        changes = {
            key: local_to_remote_input(local[key]) if key == "input_template" else local[key]
            for key in selected
            if records[key].local_text != records[key].remote_text
        }
        snapshot = inspected.remote
        if changes:
            snapshot = self.adapter.write(
                agent_id,
                changes,
                {
                    key: (
                        inspected.remote.templates[key].version,
                        inspected.remote.templates[key].text,
                    )
                    for key in changes
                },
                expected_fingerprint=inspected.fingerprint,
            )
            if any(snapshot.templates[key].text != text for key, text in changes.items()):
                raise SyncConflictError("Logfire variable read-back changed; inspect sync again.")
        updates = {
            key: {"base_text": local[key], "remote_version": snapshot.templates[key].version}
            for key in selected
        }
        self.store.advance_agent_prompt_sync_link(
            agent_id,
            expected_link_id=link.id,
            expected_generation=link.generation,
            field_updates=updates,
            expected_active_version_id=version.id,
            expected_local_texts=local,
        )
        return self.inspect(agent_id)

    def push(
        self, agent_id: str, fields: Sequence[str] | None, expected_revision: str
    ) -> SyncStatus:
        """Publish local-only changes and advance the cursor after read-back."""
        inspected = self._checked(agent_id, expected_revision)
        self._require_link(inspected)
        self._require_valid_local(self._require_version(inspected))
        selected = self._selected(inspected, fields, {"local_changed"})
        return self._push(agent_id, inspected, selected)

    def resolve(
        self, agent_id: str, fields: Sequence[str], choice: str, expected_revision: str
    ) -> SyncStatus:
        """Apply an explicit local or remote choice to conflicted fields."""
        inspected = self._checked(agent_id, expected_revision)
        self._require_link(inspected)
        self._require_valid_local(self._require_version(inspected))
        if choice not in ("local", "remote"):
            raise ConfigError("choice must be 'local' or 'remote'.")
        if not fields:
            raise ConfigError("resolve requires at least one field.")
        selected: list[TemplateKey] = []
        for key in fields:
            if key not in KEYS:
                raise ConfigError(f"Unknown sync field {key!r}.")
            state = inspected.status.templates[key].state
            if state not in ("conflict", "remote_missing") or (
                state == "remote_missing" and choice == "remote"
            ):
                raise ConfigError(f"{key} is {state} and cannot be resolved with {choice}.")
            if key not in selected:
                selected.append(key)  # type: ignore[arg-type]
        return (
            self._push(agent_id, inspected, selected)
            if choice == "local"
            else self._pull(agent_id, inspected, selected)
        )

    def unlink(self, agent_id: str, expected_revision: str) -> SyncStatus:
        """Delete the local cursor, including after a configured-key change."""
        inspected = self._checked(agent_id, expected_revision, unlink=True)
        link = self._require_link(inspected)
        self.store.delete_agent_prompt_sync_link(
            agent_id, expected_link_id=link.id, expected_generation=link.generation
        )
        # A rotated key may point at a different project. The Unlink response
        # remains local-only; a subsequent explicit Inspect can preview it.
        return self._inspect(agent_id, read_remote=inspected.status.error is None).status
