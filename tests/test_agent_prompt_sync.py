"""Tests for the agent prompt sync service (``valcore.agent_prompt_sync``).

Pins the explicit Inspect / Link / Pull / Push / Resolve / Unlink operations that move an agent's
two text templates (``instructions`` and ``input_template``) between local ``AgentVersion`` rows
and Logfire managed variables. The store and a *fake* adapter are the only collaborators; no test
touches Logfire.

Assumed public shapes (the task text fixes the operation names and the status vocabulary, not the
constructor or the container types):

- ``AgentPromptSync(store, adapter, key_fingerprint)`` where ``key_fingerprint`` is a zero-arg
  callable returning the configured key's nonreversible fingerprint, or ``None`` when no key is
  configured. The adapter exposes ``read(agent_id)`` and ``write(agent_id, changes,
  expected_versions)`` exactly like ``PromptVariableAdapter``.
- ``inspect`` returns a status object with ``linked``, ``local_version_id``, ``revision``,
  ``error`` (``None`` or a human-readable message) and ``templates``: a mapping from
  ``"instructions"`` / ``"input_template"`` to a record with ``variable_name``, ``state``,
  ``local_text``, ``base_text``, ``remote_text``, ``remote_version`` and ``base_remote_version``.
- ``pull`` / ``push`` / ``resolve`` / ``link`` / ``unlink`` take ``fields=None`` meaning "every
  eligible key". Ineligible explicit fields and other invalid requests raise ``ConfigError`` whose
  message names the offending field; stale revisions and racing writers raise ``SyncConflictError``.
- Texts that are equal on both sides but whose cursor is stale (both sides changed to identical
  text, or a remote write landed but the cursor update failed) report state ``in_sync``; Pull or
  Push of such a key advances the cursor without creating a version or writing remotely.
"""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from valcore.agent_prompt_sync import AgentPromptSync
from valcore.errors import ConfigError, SyncConflictError, ValcoreError
from valcore.logfire_prompt_variables import RemoteSnapshot, RemoteTemplate
from valcore.store import Store, create_engine, init_db

FP = "fingerprint-of-key-a-4f2c"
OTHER_FP = "fingerprint-of-key-b-9d81"
KEYS = ("instructions", "input_template")

BASE_INSTR = "You are a helpful assistant."
BASE_TMPL = "Answer {question}."
BASE_TMPL_REMOTE = "Answer {{question}}."


# -- Fakes and fixtures --------------------------------------------------------------------------


class FakeAdapter:
    """In-memory stand-in for ``PromptVariableAdapter`` with hooks for racing writers."""

    def __init__(self) -> None:
        self.vars: dict[str, tuple[int, str]] = {}
        self.reads = 0
        self.writes: list[dict[str, str]] = []
        self.fail_write: Exception | None = None
        self.after_read: Callable[[], None] | None = None
        self.after_write: Callable[[], None] | None = None
        self.fingerprint: Callable[[], str | None] = lambda: FP

    @staticmethod
    def name(agent_id: str, key: str) -> str:
        return f"valcore_agent_{agent_id}_{key}"

    def _snapshot(self, agent_id: str) -> RemoteSnapshot:
        templates = {}
        for key in KEYS:
            name = self.name(agent_id, key)
            version, text = self.vars.get(name, (None, None))
            templates[key] = RemoteTemplate(name, version, text)
        return RemoteSnapshot(templates)

    def read(self, agent_id: str, *, expected_fingerprint: str | None = None) -> RemoteSnapshot:
        if expected_fingerprint is not None and self.fingerprint() != expected_fingerprint:
            raise SyncConflictError("Logfire key changed; inspect again.")
        self.reads += 1
        snapshot = self._snapshot(agent_id)
        hook, self.after_read = self.after_read, None
        if hook is not None:
            hook()
        return snapshot

    def write(
        self,
        agent_id: str,
        changes: dict[str, str],
        expected_versions: dict[str, tuple[int | None, str | None]],
        *,
        expected_fingerprint: str | None = None,
    ) -> RemoteSnapshot:
        if expected_fingerprint is not None and self.fingerprint() != expected_fingerprint:
            raise SyncConflictError("Logfire key changed; inspect again.")
        if self.fail_write is not None:
            exc, self.fail_write = self.fail_write, None
            raise exc
        before = self._snapshot(agent_id)
        for key in changes:
            actual = before.templates[key]
            if (actual.version, actual.text) != expected_versions[key]:
                raise SyncConflictError(f"{actual.variable_name} changed; inspect again.")
        self.writes.append(dict(changes))
        for key, text in changes.items():
            name = self.name(agent_id, key)
            self.vars[name] = ((self.vars[name][0] if name in self.vars else 0) + 1, text)
        hook, self.after_write = self.after_write, None
        if hook is not None:
            hook()
        return self._snapshot(agent_id)


@dataclass
class Key:
    """Mutable configured-key fingerprint; ``None`` means no key configured."""

    value: str | None = FP


@dataclass
class Env:
    store: Store
    adapter: FakeAdapter
    key: Key
    svc: AgentPromptSync
    agent_id: str
    version_id: str

    # remote helpers ---------------------------------------------------------
    def set_remote(self, key: str, text: str) -> None:
        name = self.adapter.name(self.agent_id, key)
        version = self.adapter.vars[name][0] + 1 if name in self.adapter.vars else 1
        self.adapter.vars[name] = (version, text)

    def delete_remote(self, key: str) -> None:
        del self.adapter.vars[self.adapter.name(self.agent_id, key)]

    def remote_version(self, key: str) -> int | None:
        entry = self.adapter.vars.get(self.adapter.name(self.agent_id, key))
        return entry[0] if entry else None

    # local helpers ----------------------------------------------------------
    def active_version(self):
        agent = self.store.get_agent(self.agent_id)
        return self.store.get_agent_version(agent.active_version_id)

    def edit_local(self, *, instructions: str | None = None, template: str | None = None) -> None:
        """Edit the active (unfrozen) version in place, keeping its ID."""
        version = self.active_version()
        fields: dict[str, Any] = {}
        if instructions is not None:
            fields["spec"] = {**version.spec, "instructions": instructions}
        if template is not None:
            fields["prompt_template"] = template
        self.store.update_agent_version(version.id, **fields)

    def new_local_version(self, **overrides: Any):
        """Create a *new* active version copied from the active one with overrides applied."""
        source = self.active_version()
        fields: dict[str, Any] = {
            "version_name": f"local-{len(self.store.list_agent_versions(self.agent_id)) + 1}",
            "model": source.model,
            "spec": dict(source.spec),
            "prompt_template": source.prompt_template,
            "required_columns": list(source.required_columns),
            "deps_mapping": dict(source.deps_mapping),
        }
        fields.update(overrides)
        return self.store.create_agent_version(self.agent_id, **fields)

    def cursor(self):
        return self.store.get_agent_prompt_sync_link(self.agent_id)

    # service helpers --------------------------------------------------------
    def status(self):
        return self.svc.inspect(self.agent_id)

    def rev(self) -> str:
        return self.status().revision

    def state(self, key: str) -> str:
        return self.status().templates[key].state

    def link(self, initial: str = "local"):
        return self.svc.link(self.agent_id, initial, self.rev())

    def pull(self, fields: list[str] | None = None):
        return self.svc.pull(self.agent_id, fields, self.rev())

    def push(self, fields: list[str] | None = None):
        return self.svc.push(self.agent_id, fields, self.rev())

    def resolve(self, fields: list[str], choice: str):
        return self.svc.resolve(self.agent_id, fields, choice, self.rev())

    def unlink(self):
        return self.svc.unlink(self.agent_id, self.rev())


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    engine = create_engine(tmp_path / "sync.db")
    init_db(engine)
    try:
        yield Store(engine)
    finally:
        engine.dispose()


def build_env(store: Store, *, with_version: bool = True, **version_overrides: Any) -> Env:
    agent = store.create_agent("subject")
    version_id = ""
    if with_version:
        fields: dict[str, Any] = {
            "version_name": "v1",
            "model": "gateway/anthropic:claude-sonnet-5",
            "spec": {"instructions": BASE_INSTR, "retries": 2},
            "prompt_template": BASE_TMPL,
            "required_columns": ["question", "context"],
            "deps_mapping": {},
            "notes": "keep me",
        }
        fields.update(version_overrides)
        version_id = store.create_agent_version(agent.id, **fields).id
    adapter = FakeAdapter()
    key = Key()
    adapter.fingerprint = lambda: key.value
    svc = AgentPromptSync(store, adapter, lambda: key.value)
    return Env(store, adapter, key, svc, agent.id, version_id)


@pytest.fixture
def env(store: Store) -> Env:
    """An agent with one unfrozen version, no remote variables, and no link."""
    return build_env(store)


@pytest.fixture
def linked(env: Env) -> Env:
    """The same agent, linked with ``initial="local"`` so both sides hold the base texts."""
    env.link("local")
    env.adapter.writes.clear()
    env.adapter.reads = 0
    return env


# -- Inspect -------------------------------------------------------------------------------------


def test_inspect_unlinked_with_missing_remote_reports_remote_missing_preview(env: Env) -> None:
    status = env.status()

    assert status.linked is False
    assert status.error is None
    assert status.local_version_id == env.version_id
    for key in KEYS:
        record = status.templates[key]
        assert record.state == "remote_missing"
        assert record.variable_name == f"valcore_agent_{env.agent_id}_{key}"
        assert record.remote_text is None
        assert record.remote_version is None
        assert record.base_text is None
        assert record.base_remote_version is None
    assert status.templates["instructions"].local_text == BASE_INSTR
    assert status.templates["input_template"].local_text == BASE_TMPL


def test_inspect_unlinked_previews_remote_text_in_valcore_format(env: Env) -> None:
    env.set_remote("instructions", "Remote instructions")
    env.set_remote("input_template", "Reply to {{question}}.")

    status = env.status()

    assert status.templates["instructions"].remote_text == "Remote instructions"
    assert status.templates["input_template"].remote_text == "Reply to {question}."
    assert status.templates["input_template"].remote_version == 1


def test_inspect_is_read_only(linked: Env) -> None:
    before_cursor = linked.cursor()
    before_versions = linked.store.list_agent_versions(linked.agent_id)

    linked.status()
    linked.status()

    after_cursor = linked.cursor()
    assert (after_cursor.id, after_cursor.generation) == (
        before_cursor.id,
        before_cursor.generation,
    )
    assert len(linked.store.list_agent_versions(linked.agent_id)) == len(before_versions)
    assert linked.adapter.writes == []


def test_inspect_linked_in_sync_records_cursor_baselines(linked: Env) -> None:
    status = linked.status()

    assert status.linked is True
    for key in KEYS:
        assert status.templates[key].state == "in_sync"
        assert status.templates[key].remote_version == 1
        assert status.templates[key].base_remote_version == 1
    tmpl = status.templates["input_template"]
    assert tmpl.local_text == BASE_TMPL
    assert tmpl.base_text == BASE_TMPL  # cursor baseline is valcore format
    assert tmpl.remote_text == BASE_TMPL  # remote text is converted back to valcore format
    assert linked.adapter.vars[linked.adapter.name(linked.agent_id, "input_template")][1] == (
        BASE_TMPL_REMOTE
    )


def test_inspect_local_changed(linked: Env) -> None:
    linked.edit_local(instructions="Local edit")

    status = linked.status()

    record = status.templates["instructions"]
    assert record.state == "local_changed"
    assert record.local_text == "Local edit"
    assert record.base_text == BASE_INSTR
    assert record.remote_text == BASE_INSTR
    assert status.templates["input_template"].state == "in_sync"


def test_inspect_remote_changed(linked: Env) -> None:
    linked.set_remote("input_template", "Please answer {{question}} using {{context}}.")

    record = linked.status().templates["input_template"]

    assert record.state == "remote_changed"
    assert record.remote_text == "Please answer {question} using {context}."
    assert record.remote_version == 2
    assert record.base_remote_version == 1
    assert record.local_text == BASE_TMPL
    assert linked.state("instructions") == "in_sync"


def test_inspect_conflict_carries_local_base_and_remote_text(linked: Env) -> None:
    linked.edit_local(instructions="Local side")
    linked.set_remote("instructions", "Remote side")

    record = linked.status().templates["instructions"]

    assert record.state == "conflict"
    assert (record.local_text, record.base_text, record.remote_text) == (
        "Local side",
        BASE_INSTR,
        "Remote side",
    )


def test_inspect_both_fields_can_change_independently(linked: Env) -> None:
    linked.edit_local(instructions="Local instructions")
    linked.set_remote("input_template", "Remote {{question}}")

    status = linked.status()

    assert status.templates["instructions"].state == "local_changed"
    assert status.templates["input_template"].state == "remote_changed"


def test_inspect_remote_deleted_after_link_is_remote_missing(linked: Env) -> None:
    linked.delete_remote("instructions")

    record = linked.status().templates["instructions"]

    assert record.state == "remote_missing"
    assert record.remote_text is None
    assert record.remote_version is None
    assert record.base_text == BASE_INSTR
    assert linked.state("input_template") == "in_sync"


def test_inspect_empty_remote_string_is_not_missing(linked: Env) -> None:
    linked.set_remote("input_template", "")

    record = linked.status().templates["input_template"]

    assert record.state == "remote_changed"
    assert record.remote_text == ""


def test_inspect_without_active_version_is_unsupported(store: Store) -> None:
    env = build_env(store, with_version=False)

    status = env.status()

    assert status.local_version_id is None
    assert {status.templates[key].state for key in KEYS} == {"unsupported"}
    assert env.adapter.writes == []


@pytest.mark.parametrize(
    "spec",
    [
        {},
        {"instructions": None},
        {"instructions": ["first", "second"]},
        {"instructions": "Hello {{name}}"},
        {"instructions": "Use @{other_variable}@"},
    ],
    ids=["missing", "null", "list", "logfire-variable", "composition-reference"],
)
def test_inspect_unsupported_instructions(store: Store, spec: dict) -> None:
    env = build_env(store, spec=spec)

    assert env.state("instructions") == "unsupported"


def test_inspect_empty_instructions_are_valid(store: Store) -> None:
    env = build_env(store, spec={"instructions": ""})

    assert env.state("instructions") == "remote_missing"


def test_inspect_unsupported_local_input_template(store: Store) -> None:
    env = build_env(store, prompt_template="Answer {question!r}.")

    assert env.state("input_template") == "unsupported"
    assert env.state("instructions") == "remote_missing"


def test_inspect_unsupported_remote_input_template(linked: Env) -> None:
    linked.set_remote("input_template", "{{#if question}}Answer {{question}}{{/if}}")

    assert linked.state("input_template") == "unsupported"


def test_unsupported_remote_template_explains_the_reason(linked: Env) -> None:
    linked.set_remote("input_template", "Answer {{unknown}}.")

    record = linked.status().templates["input_template"]

    assert record.state == "unsupported"
    assert record.error is not None
    assert "unknown" in record.error
    with pytest.raises(ConfigError, match="unknown"):
        linked.pull(["input_template"])


def test_inspect_remote_template_with_undeclared_column_is_unsupported(linked: Env) -> None:
    linked.set_remote("input_template", "Answer {{question}} about {{topic}}.")

    assert linked.state("input_template") == "unsupported"


def test_inspect_without_configured_key_reports_error_and_makes_no_remote_call(env: Env) -> None:
    env.key.value = None

    status = env.status()

    assert status.error is not None
    assert "project:read_variables" in status.error
    assert "project:write_variables" in status.error
    assert env.adapter.reads == 0
    for key in KEYS:
        assert status.templates[key].remote_text is None
        assert status.templates[key].remote_version is None


def test_inspect_with_changed_key_blocks_remote_read_and_asks_for_unlink(linked: Env) -> None:
    linked.key.value = OTHER_FP

    status = linked.status()

    assert linked.adapter.reads == 0
    assert status.linked is True
    assert status.error
    assert "unlink" in status.error.lower()
    assert status.revision
    for key in KEYS:
        assert status.templates[key].remote_text is None


def test_inspect_never_exposes_the_key_fingerprint(linked: Env) -> None:
    statuses = [linked.status()]
    linked.key.value = OTHER_FP
    statuses.append(linked.status())

    for status in statuses:
        assert FP not in repr(status)
        assert OTHER_FP not in repr(status)


def test_revision_is_stable_opaque_sha256(linked: Env) -> None:
    first = linked.rev()

    assert first == linked.rev()
    assert re.fullmatch(r"[0-9a-f]{64}", first)


def test_revision_changes_on_same_id_edit_of_unfrozen_version(linked: Env) -> None:
    before = linked.rev()
    version_id = linked.active_version().id

    linked.edit_local(instructions="Edited in place")

    assert linked.active_version().id == version_id
    assert linked.rev() != before


def test_revision_changes_on_remote_and_cursor_changes(linked: Env) -> None:
    baseline = linked.rev()
    linked.set_remote("instructions", "Remote edit")
    remote_changed = linked.rev()
    linked.pull(["instructions"])
    after_pull = linked.rev()

    assert len({baseline, remote_changed, after_pull}) == 3


def test_revision_changes_when_active_version_changes_even_with_same_text(linked: Env) -> None:
    before = linked.rev()

    new_version = linked.new_local_version()

    status = linked.status()
    assert status.local_version_id == new_version.id
    assert status.revision != before
    assert {status.templates[key].state for key in KEYS} == {"in_sync"}  # not a text conflict


# -- Link ----------------------------------------------------------------------------------------


def test_link_local_creates_missing_remote_variables_and_records_cursor(env: Env) -> None:
    env.link("local")

    assert env.adapter.writes == [{"instructions": BASE_INSTR, "input_template": BASE_TMPL_REMOTE}]
    cursor = env.cursor()
    assert cursor is not None
    assert cursor.key_fingerprint == FP
    assert cursor.agent_version_id == env.version_id
    assert cursor.generation == 0
    assert cursor.instructions_variable_name == f"valcore_agent_{env.agent_id}_instructions"
    assert cursor.input_template_variable_name == f"valcore_agent_{env.agent_id}_input_template"
    assert (cursor.instructions_remote_version, cursor.input_template_remote_version) == (1, 1)
    assert cursor.instructions_base_text == BASE_INSTR
    assert cursor.input_template_base_text == BASE_TMPL  # valcore format, not the remote form
    assert {env.state(key) for key in KEYS} == {"in_sync"}
    assert len(env.store.list_agent_versions(env.agent_id)) == 1


def test_link_local_with_empty_input_template_creates_empty_string_variable(store: Store) -> None:
    env = build_env(store, prompt_template="", required_columns=[])

    env.link("local")

    assert env.adapter.vars[env.adapter.name(env.agent_id, "input_template")] == (1, "")
    assert env.cursor().input_template_base_text == ""
    assert env.state("input_template") == "in_sync"


def test_link_remote_creates_new_active_version_from_remote_text(env: Env) -> None:
    env.set_remote("instructions", "Remote instructions")
    env.set_remote("input_template", "Remote {{question}} with {{context}}")
    original = env.active_version()

    env.link("remote")

    agent = env.store.get_agent(env.agent_id)
    assert agent.active_version_id != original.id
    new = env.active_version()
    assert new.spec == {**original.spec, "instructions": "Remote instructions"}
    assert new.prompt_template == "Remote {question} with {context}"
    assert (new.model, new.required_columns, new.deps_mapping, new.notes) == (
        original.model,
        original.required_columns,
        original.deps_mapping,
        original.notes,
    )
    assert new.frozen is False
    assert env.store.get_agent_version(original.id).prompt_template == BASE_TMPL
    cursor = env.cursor()
    assert cursor.agent_version_id == new.id
    assert cursor.instructions_base_text == "Remote instructions"
    assert cursor.input_template_base_text == "Remote {question} with {context}"
    assert env.adapter.writes == []
    assert {env.state(key) for key in KEYS} == {"in_sync"}


def test_link_initial_local_overwrites_differing_remote_with_a_new_remote_version(env: Env) -> None:
    env.set_remote("instructions", "Remote instructions")
    env.set_remote("input_template", "Remote {{question}}")

    env.link("local")

    assert env.remote_version("instructions") == 2
    assert env.remote_version("input_template") == 2
    assert env.cursor().instructions_remote_version == 2
    assert len(env.store.list_agent_versions(env.agent_id)) == 1


def test_link_when_both_sides_match_only_records_baseline(env: Env) -> None:
    for initial in ("local", "remote"):
        fresh = build_env(env.store)
        fresh.set_remote("instructions", BASE_INSTR)
        fresh.set_remote("input_template", BASE_TMPL_REMOTE)
        fresh.link(initial)

        assert fresh.adapter.writes == []
        assert len(env.store.list_agent_versions(fresh.agent_id)) == 1
        cursor = fresh.cursor()
        assert (cursor.instructions_remote_version, cursor.input_template_remote_version) == (1, 1)
        assert cursor.agent_version_id == fresh.version_id


def test_link_with_one_missing_remote_rejects_initial_remote(env: Env) -> None:
    env.set_remote("instructions", "Remote instructions")

    with pytest.raises(ConfigError, match="input_template"):
        env.link("remote")

    assert env.cursor() is None
    assert len(env.store.list_agent_versions(env.agent_id)) == 1
    assert env.adapter.writes == []


def test_link_local_with_one_missing_remote_writes_only_what_differs_and_creates_missing(
    env: Env,
) -> None:
    env.set_remote("instructions", "Remote instructions")

    env.link("local")

    assert env.remote_version("instructions") == 2
    assert env.remote_version("input_template") == 1
    assert env.cursor().instructions_base_text == BASE_INSTR


def test_link_rejects_unknown_initial_without_choosing_a_winner(env: Env) -> None:
    env.set_remote("instructions", "Remote instructions")
    env.set_remote("input_template", "Remote {{question}}")

    with pytest.raises((ConfigError, ValueError)):
        env.svc.link(env.agent_id, "newest", env.rev())

    assert env.cursor() is None
    assert env.adapter.writes == []
    assert len(env.store.list_agent_versions(env.agent_id)) == 1


def test_link_with_stale_revision_raises_sync_conflict(env: Env) -> None:
    stale = env.rev()
    env.edit_local(instructions="Changed after inspect")

    with pytest.raises(SyncConflictError):
        env.svc.link(env.agent_id, "local", stale)

    assert env.cursor() is None
    assert env.adapter.writes == []


def test_link_when_already_linked_is_rejected(linked: Env) -> None:
    with pytest.raises(ValcoreError):
        linked.link("local")

    assert linked.cursor().generation == 0
    assert linked.adapter.writes == []


def test_link_without_active_version_is_rejected(store: Store) -> None:
    env = build_env(store, with_version=False)

    with pytest.raises(ValcoreError):
        env.link("local")

    assert env.cursor() is None
    assert env.adapter.writes == []


def test_link_without_configured_key_is_rejected(env: Env) -> None:
    env.key.value = None

    with pytest.raises(ConfigError) as excinfo:
        env.svc.link(env.agent_id, "local", env.rev())
    assert "project:read_variables" in str(excinfo.value)
    assert "project:write_variables" in str(excinfo.value)

    assert env.cursor() is None
    assert env.adapter.writes == []


@pytest.mark.parametrize(
    "overrides,field",
    [
        ({"prompt_template": "Answer {question!r}."}, "input_template"),
        ({"prompt_template": "Answer {question:>10}."}, "input_template"),
        ({"prompt_template": "Literal {{braces}} {question}"}, "input_template"),
        ({"spec": {"instructions": None}}, "instructions"),
        ({"spec": {"instructions": ["a", "b"]}}, "instructions"),
        ({"spec": {"instructions": "Hi {{name}}"}}, "instructions"),
    ],
)
def test_link_rejects_unsupported_local_text_before_any_write(
    store: Store, overrides: dict, field: str
) -> None:
    env = build_env(store, **overrides)

    with pytest.raises(ConfigError, match=field):
        env.link("local")

    assert env.adapter.writes == []
    assert env.cursor() is None


def test_link_rejects_unsupported_remote_text_for_initial_remote(env: Env) -> None:
    env.set_remote("instructions", "Remote instructions")
    env.set_remote("input_template", "{{#if question}}x{{/if}}")

    with pytest.raises(ConfigError, match="input_template"):
        env.link("remote")

    assert env.cursor() is None
    assert len(env.store.list_agent_versions(env.agent_id)) == 1


def test_link_remote_with_undeclared_column_is_rejected_atomically(env: Env) -> None:
    env.set_remote("instructions", "Remote instructions")
    env.set_remote("input_template", "Answer {{question}} and {{nonexistent}}")

    with pytest.raises(ConfigError):
        env.link("remote")

    assert env.cursor() is None
    assert len(env.store.list_agent_versions(env.agent_id)) == 1
    assert env.active_version().spec["instructions"] == BASE_INSTR


def test_link_retry_after_cursor_failure_does_not_write_another_version(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = env.store.create_agent_prompt_sync_link
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValcoreError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(env.store, "create_agent_prompt_sync_link", flaky)

    with pytest.raises(ValcoreError, match="disk full"):
        env.link("local")
    assert env.cursor() is None
    assert len(env.adapter.writes) == 1

    env.link("local")  # retry re-inspects and links the matching remote text

    assert len(env.adapter.writes) == 1
    assert env.remote_version("instructions") == 1
    assert env.remote_version("input_template") == 1
    cursor = env.cursor()
    assert (cursor.instructions_remote_version, cursor.input_template_remote_version) == (1, 1)
    assert {env.state(key) for key in KEYS} == {"in_sync"}


def test_link_local_rejects_key_rotation_between_inspect_and_write(env: Env) -> None:
    revision = env.rev()
    env.adapter.after_read = lambda: setattr(env.key, "value", OTHER_FP)

    with pytest.raises(SyncConflictError):
        env.svc.link(env.agent_id, "local", revision)

    assert env.adapter.writes == []
    assert env.cursor() is None


def test_remote_first_link_rejects_concurrent_non_template_edit(env: Env) -> None:
    env.set_remote("instructions", "Remote instructions")
    env.set_remote("input_template", BASE_TMPL_REMOTE)
    revision = env.rev()
    original = env.store.create_agent_prompt_sync_link

    def racing_link(*args: Any, **kwargs: Any):
        env.store.update_agent_version(env.version_id, notes="concurrent notes")
        return original(*args, **kwargs)

    env.store.create_agent_prompt_sync_link = racing_link  # type: ignore[method-assign]
    with pytest.raises(SyncConflictError):
        env.svc.link(env.agent_id, "remote", revision)

    assert env.cursor() is None
    assert env.active_version().notes == "concurrent notes"


def test_link_failed_remote_write_leaves_no_cursor(env: Env) -> None:
    env.adapter.fail_write = ValcoreError("Logfire managed-variable request failed.")

    with pytest.raises(ValcoreError):
        env.link("local")

    assert env.cursor() is None
    assert env.adapter.vars == {}


# -- Pull ----------------------------------------------------------------------------------------


def test_pull_creates_new_active_version_copying_everything_but_the_text(linked: Env) -> None:
    source = linked.active_version()
    linked.store.update_agent_version(source.id, notes="source notes")
    linked.set_remote("instructions", "Remote instructions")
    linked.set_remote("input_template", "Reply to {{question}} with {{context}}.")

    linked.pull()

    versions = linked.store.list_agent_versions(linked.agent_id)
    assert len(versions) == 2
    new = linked.active_version()
    assert new.id != source.id
    assert new.frozen is False
    assert new.spec == {**source.spec, "instructions": "Remote instructions"}
    assert new.spec["retries"] == 2  # other spec keys are copied verbatim
    assert new.prompt_template == "Reply to {question} with {context}."
    assert (new.model, new.required_columns, new.deps_mapping, new.notes) == (
        source.model,
        source.required_columns,
        source.deps_mapping,
        "source notes",
    )
    unchanged = linked.store.get_agent_version(source.id)
    assert unchanged.spec["instructions"] == BASE_INSTR
    assert unchanged.prompt_template == BASE_TMPL


def test_pull_advances_cursor_for_pulled_keys(linked: Env) -> None:
    before = linked.cursor()
    linked.set_remote("instructions", "Remote instructions")

    linked.pull()

    cursor = linked.cursor()
    assert cursor.id == before.id
    assert cursor.generation == before.generation + 1
    assert cursor.agent_version_id == linked.active_version().id
    assert cursor.instructions_base_text == "Remote instructions"
    assert cursor.instructions_remote_version == 2
    assert {linked.state(key) for key in KEYS} == {"in_sync"}


def test_pull_stores_input_template_baseline_in_valcore_format(linked: Env) -> None:
    linked.set_remote("input_template", "Please answer {{question}}.")

    linked.pull()

    assert linked.cursor().input_template_base_text == "Please answer {question}."
    assert linked.active_version().prompt_template == "Please answer {question}."


def test_pull_never_mutates_a_frozen_source_version(linked: Env) -> None:
    source_id = linked.active_version().id
    linked.store.freeze_agent_version(source_id)
    linked.set_remote("instructions", "Remote instructions")

    linked.pull()

    frozen = linked.store.get_agent_version(source_id)
    assert frozen.frozen is True
    assert frozen.spec["instructions"] == BASE_INSTR
    assert linked.active_version().id != source_id
    assert linked.active_version().frozen is False


def test_pull_assigns_unique_readable_version_names(linked: Env) -> None:
    linked.set_remote("instructions", "Remote one")
    linked.pull()
    linked.set_remote("instructions", "Remote two")
    linked.pull()

    names = [version.version_name for version in linked.store.list_agent_versions(linked.agent_id)]

    assert len(names) == 3
    assert len(set(names)) == 3
    assert all(isinstance(name, str) and name.strip() for name in names)


def test_pull_retains_baseline_and_remote_version_of_unselected_field(linked: Env) -> None:
    linked.set_remote("instructions", "Remote instructions")
    linked.set_remote("input_template", "Remote {{question}}")
    before = linked.cursor()

    linked.pull(["instructions"])

    cursor = linked.cursor()
    assert cursor.instructions_base_text == "Remote instructions"
    assert cursor.instructions_remote_version == 2
    assert cursor.input_template_base_text == before.input_template_base_text == BASE_TMPL
    assert cursor.input_template_remote_version == before.input_template_remote_version == 1
    assert linked.active_version().prompt_template == BASE_TMPL
    assert linked.state("input_template") == "remote_changed"


def test_pull_default_takes_only_remote_changed_and_leaves_local_changes_alone(
    linked: Env,
) -> None:
    linked.set_remote("instructions", "Remote instructions")
    linked.edit_local(template="Local {question}")

    linked.pull()

    new = linked.active_version()
    assert new.spec["instructions"] == "Remote instructions"
    assert new.prompt_template == "Local {question}"
    assert linked.state("input_template") == "local_changed"
    assert linked.cursor().input_template_base_text == BASE_TMPL


def test_mixed_directions_pull_one_field_then_push_the_other(linked: Env) -> None:
    linked.set_remote("instructions", "Remote instructions")
    linked.edit_local(template="Local {question}")

    linked.pull(["instructions"])
    linked.push(["input_template"])

    assert linked.adapter.writes == [{"input_template": "Local {{question}}"}]
    active = linked.active_version()
    assert active.spec["instructions"] == "Remote instructions"
    assert active.prompt_template == "Local {question}"
    cursor = linked.cursor()
    assert cursor.instructions_base_text == "Remote instructions"
    assert cursor.input_template_base_text == "Local {question}"
    assert cursor.agent_version_id == active.id
    assert {linked.state(key) for key in KEYS} == {"in_sync"}


@pytest.mark.parametrize("state_setup", ["local_changed", "conflict", "in_sync", "remote_missing"])
def test_pull_rejects_explicit_ineligible_field_with_field_specific_error(
    linked: Env, state_setup: str
) -> None:
    if state_setup == "local_changed":
        linked.edit_local(instructions="Local edit")
    elif state_setup == "conflict":
        linked.edit_local(instructions="Local edit")
        linked.set_remote("instructions", "Remote edit")
    elif state_setup == "remote_missing":
        linked.delete_remote("instructions")
    before = linked.cursor()
    versions = len(linked.store.list_agent_versions(linked.agent_id))

    with pytest.raises(ConfigError, match="instructions"):
        linked.pull(["instructions"])

    assert linked.cursor().generation == before.generation
    assert len(linked.store.list_agent_versions(linked.agent_id)) == versions


def test_pull_with_one_ineligible_field_makes_no_partial_change(linked: Env) -> None:
    linked.set_remote("instructions", "Remote instructions")  # eligible
    linked.edit_local(template="Local {question}")  # ineligible for pull

    with pytest.raises(ConfigError, match="input_template"):
        linked.pull(["instructions", "input_template"])

    assert linked.active_version().spec["instructions"] == BASE_INSTR
    assert linked.cursor().generation == 0
    assert len(linked.store.list_agent_versions(linked.agent_id)) == 1


def test_pull_rejects_unknown_field_name(linked: Env) -> None:
    linked.set_remote("instructions", "Remote instructions")

    with pytest.raises((ConfigError, ValueError), match="model"):
        linked.pull(["model"])

    assert len(linked.store.list_agent_versions(linked.agent_id)) == 1


def test_pull_of_remote_template_missing_required_column_is_unsupported_and_not_partial(
    linked: Env,
) -> None:
    linked.set_remote("instructions", "Remote instructions")
    linked.set_remote("input_template", "Answer {{question}} about {{topic}}.")
    assert linked.state("input_template") == "unsupported"

    with pytest.raises(ConfigError, match="input_template"):
        linked.pull(["instructions", "input_template"])
    with pytest.raises(ConfigError):
        linked.pull(["input_template"])

    assert len(linked.store.list_agent_versions(linked.agent_id)) == 1
    assert linked.active_version().spec["instructions"] == BASE_INSTR
    assert linked.cursor().generation == 0
    assert linked.cursor().instructions_remote_version == 1


def test_pull_of_unsupported_remote_syntax_is_rejected(linked: Env) -> None:
    linked.set_remote("input_template", "{{#each items}}{{this}}{{/each}}")

    with pytest.raises(ConfigError, match="input_template"):
        linked.pull(["input_template"])

    assert len(linked.store.list_agent_versions(linked.agent_id)) == 1


def test_pull_with_stale_revision_raises_sync_conflict(linked: Env) -> None:
    linked.set_remote("instructions", "Remote instructions")
    stale = linked.rev()
    linked.set_remote("instructions", "Remote instructions, again")

    with pytest.raises(SyncConflictError):
        linked.svc.pull(linked.agent_id, None, stale)

    assert len(linked.store.list_agent_versions(linked.agent_id)) == 1


def test_pull_rejects_same_id_edit_of_unfrozen_version_after_inspect(linked: Env) -> None:
    linked.set_remote("instructions", "Remote instructions")
    stale = linked.rev()
    linked.edit_local(template="Edited {question}")  # same version ID, new text

    with pytest.raises(SyncConflictError):
        linked.svc.pull(linked.agent_id, None, stale)

    assert len(linked.store.list_agent_versions(linked.agent_id)) == 1
    assert linked.cursor().generation == 0


def test_pull_rejects_concurrent_non_template_edit(linked: Env) -> None:
    linked.set_remote("instructions", "Remote instructions")
    revision = linked.rev()
    original = linked.store.create_agent_version_and_advance_prompt_sync_link

    def racing_pull(*args: Any, **kwargs: Any):
        linked.store.update_agent_version(linked.version_id, model="gateway/openai:gpt-5")
        return original(*args, **kwargs)

    linked.store.create_agent_version_and_advance_prompt_sync_link = racing_pull  # type: ignore[method-assign]
    with pytest.raises(SyncConflictError):
        linked.svc.pull(linked.agent_id, None, revision)

    assert linked.active_version().model == "gateway/openai:gpt-5"
    assert len(linked.store.list_agent_versions(linked.agent_id)) == 1
    assert linked.cursor().generation == 0


def test_push_rejects_key_rotation_between_inspect_and_write(linked: Env) -> None:
    linked.edit_local(instructions="Local instructions")
    revision = linked.rev()
    linked.adapter.after_read = lambda: setattr(linked.key, "value", OTHER_FP)

    with pytest.raises(SyncConflictError):
        linked.svc.push(linked.agent_id, None, revision)

    assert linked.adapter.writes == []
    assert linked.cursor().generation == 0


def test_pull_rejects_when_active_version_deleted_after_inspect(linked: Env) -> None:
    linked.new_local_version(version_name="second")
    linked.set_remote("instructions", "Remote instructions")
    stale = linked.rev()
    linked.store.delete_agent_version(linked.active_version().id)

    with pytest.raises(SyncConflictError):
        linked.svc.pull(linked.agent_id, None, stale)

    assert linked.cursor().generation == 0


def test_inspect_after_deleting_the_only_active_version_is_unsupported(linked: Env) -> None:
    linked.store.delete_agent_version(linked.active_version().id)

    status = linked.status()

    assert status.local_version_id is None
    assert {status.templates[key].state for key in KEYS} == {"unsupported"}
    for operation in (linked.pull, linked.push):
        with pytest.raises(ValcoreError):
            operation()
    with pytest.raises(ValcoreError):
        linked.resolve(["instructions"], "local")


def test_pull_with_changed_key_is_blocked_without_remote_read(linked: Env) -> None:
    linked.set_remote("instructions", "Remote instructions")
    linked.key.value = OTHER_FP
    reads = linked.adapter.reads

    with pytest.raises(ValcoreError, match="(?i)unlink"):
        linked.pull()

    assert linked.adapter.reads == reads
    assert len(linked.store.list_agent_versions(linked.agent_id)) == 1
    assert linked.cursor().generation == 0


def test_pull_without_configured_key_is_rejected(linked: Env) -> None:
    linked.set_remote("instructions", "Remote instructions")
    linked.key.value = None

    with pytest.raises(ConfigError):
        linked.pull()

    assert len(linked.store.list_agent_versions(linked.agent_id)) == 1


def test_pull_when_never_linked_is_rejected(env: Env) -> None:
    env.set_remote("instructions", "Remote instructions")

    with pytest.raises(ValcoreError):
        env.pull()

    assert len(env.store.list_agent_versions(env.agent_id)) == 1


def test_pull_of_equal_text_on_both_sides_reconciles_cursor_without_new_version(
    linked: Env,
) -> None:
    linked.edit_local(instructions="Converged text")
    linked.set_remote("instructions", "Converged text")
    status = linked.status()
    assert status.templates["instructions"].state == "in_sync"
    assert status.templates["instructions"].base_text == BASE_INSTR  # stale cursor

    linked.pull()

    assert len(linked.store.list_agent_versions(linked.agent_id)) == 1
    cursor = linked.cursor()
    assert cursor.instructions_base_text == "Converged text"
    assert cursor.instructions_remote_version == 2
    assert cursor.generation == 1
    assert linked.adapter.writes == []
    # after reconciling, a further local edit is an ordinary local change, not a conflict
    linked.edit_local(instructions="Next local edit")
    assert linked.state("instructions") == "local_changed"


def test_pull_records_the_new_version_id_in_the_cursor(linked: Env) -> None:
    linked.set_remote("instructions", "Remote instructions")
    original_id = linked.active_version().id

    linked.pull()

    assert linked.cursor().agent_version_id not in (None, original_id)
    assert linked.cursor().agent_version_id == linked.active_version().id


def test_pull_after_unlink_and_relink_race_leaves_replacement_link_untouched(
    linked: Env,
) -> None:
    """A stale Pull that raced Unlink + Link must not touch the generation-zero replacement."""
    linked.set_remote("instructions", "Remote instructions")
    old = linked.cursor()
    version_id = linked.active_version().id
    stale_rev = linked.rev()

    def unlink_and_relink() -> None:
        replace_link(linked, old)

    linked.adapter.after_read = unlink_and_relink

    with pytest.raises(SyncConflictError):
        linked.svc.pull(linked.agent_id, None, stale_rev)

    replacement = linked.cursor()
    assert replacement.id != old.id
    assert replacement.generation == 0
    assert replacement.instructions_base_text == BASE_INSTR
    assert len(linked.store.list_agent_versions(linked.agent_id)) == 1
    assert linked.active_version().id == version_id


def replace_link(env: Env, old) -> None:
    """Simulate another client unlinking then relinking with identical baselines."""
    env.store.delete_agent_prompt_sync_link(
        env.agent_id, expected_link_id=old.id, expected_generation=old.generation
    )
    env.store.create_agent_prompt_sync_link(
        env.agent_id,
        key_fingerprint=FP,
        instructions_variable_name=old.instructions_variable_name,
        input_template_variable_name=old.input_template_variable_name,
        instructions_remote_version=old.instructions_remote_version,
        input_template_remote_version=old.input_template_remote_version,
        instructions_base_text=old.instructions_base_text,
        input_template_base_text=old.input_template_base_text,
        expected_active_version_id=env.active_version().id,
        expected_local_texts={
            "instructions": env.active_version().spec["instructions"],
            "input_template": env.active_version().prompt_template,
        },
    )


# -- Push ----------------------------------------------------------------------------------------


def test_push_writes_only_changed_keys_converted_to_remote_format(linked: Env) -> None:
    linked.edit_local(template="Respond to {question} given {context}.")

    linked.push()

    assert linked.adapter.writes == [
        {"input_template": "Respond to {{question}} given {{context}}."}
    ]
    assert linked.remote_version("input_template") == 2
    assert linked.remote_version("instructions") == 1  # untouched variable


def test_push_does_not_create_a_local_version_and_advances_cursor(linked: Env) -> None:
    linked.edit_local(instructions="Local instructions")
    before = linked.cursor()
    active_id = linked.active_version().id

    linked.push()

    assert len(linked.store.list_agent_versions(linked.agent_id)) == 1
    cursor = linked.cursor()
    assert cursor.id == before.id
    assert cursor.generation == before.generation + 1
    assert cursor.instructions_base_text == "Local instructions"
    assert cursor.instructions_remote_version == 2
    assert cursor.agent_version_id == active_id
    assert {linked.state(key) for key in KEYS} == {"in_sync"}


def test_push_records_the_active_version_actually_published(linked: Env) -> None:
    new_version = linked.new_local_version(
        spec={"instructions": "Newly versioned instructions", "retries": 2}
    )

    linked.push()

    assert linked.cursor().agent_version_id == new_version.id
    assert linked.cursor().instructions_base_text == "Newly versioned instructions"


def test_push_retains_baseline_and_remote_version_of_unselected_field(linked: Env) -> None:
    linked.edit_local(instructions="Local instructions", template="Local {question}")
    before = linked.cursor()

    linked.push(["instructions"])

    cursor = linked.cursor()
    assert cursor.instructions_base_text == "Local instructions"
    assert cursor.input_template_base_text == before.input_template_base_text == BASE_TMPL
    assert cursor.input_template_remote_version == before.input_template_remote_version == 1
    assert linked.adapter.writes == [{"instructions": "Local instructions"}]
    assert linked.state("input_template") == "local_changed"


def test_push_uses_valcore_sync_semantics_only_via_the_adapter(linked: Env) -> None:
    """Push passes each key's inspected remote version and raw text as the write precondition."""
    seen: list[dict] = []
    real_write = linked.adapter.write

    def spy(agent_id: str, changes: dict, expected: dict, **kwargs: Any):
        seen.append({"changes": dict(changes), "expected": dict(expected)})
        return real_write(agent_id, changes, expected, **kwargs)

    linked.adapter.write = spy  # type: ignore[method-assign]
    linked.edit_local(template="Local {question}")

    linked.push()

    assert seen == [
        {
            "changes": {"input_template": "Local {{question}}"},
            "expected": {"input_template": (1, BASE_TMPL_REMOTE)},
        }
    ]


@pytest.mark.parametrize("state_setup", ["remote_changed", "conflict", "in_sync", "remote_missing"])
def test_push_rejects_explicit_ineligible_field_with_field_specific_error(
    linked: Env, state_setup: str
) -> None:
    if state_setup == "remote_changed":
        linked.set_remote("instructions", "Remote edit")
    elif state_setup == "conflict":
        linked.edit_local(instructions="Local edit")
        linked.set_remote("instructions", "Remote edit")
    elif state_setup == "remote_missing":
        linked.delete_remote("instructions")
    writes = len(linked.adapter.writes)
    generation = linked.cursor().generation

    with pytest.raises(ConfigError, match="instructions"):
        linked.push(["instructions"])

    assert len(linked.adapter.writes) == writes
    assert linked.cursor().generation == generation


def test_push_with_one_ineligible_field_writes_nothing(linked: Env) -> None:
    linked.edit_local(instructions="Local edit")  # eligible
    linked.set_remote("input_template", "Remote {{question}}")  # ineligible for push

    with pytest.raises(ConfigError, match="input_template"):
        linked.push(["instructions", "input_template"])

    assert linked.adapter.writes == []
    assert linked.cursor().generation == 0


def test_push_default_skips_conflicts_and_remote_changes(linked: Env) -> None:
    linked.edit_local(instructions="Local instructions")
    linked.set_remote("input_template", "Remote {{question}}")

    linked.push()

    assert linked.adapter.writes == [{"instructions": "Local instructions"}]
    assert linked.state("input_template") == "remote_changed"


def test_push_never_overwrites_a_conflict(linked: Env) -> None:
    linked.edit_local(instructions="Local edit")
    linked.set_remote("instructions", "Remote edit")

    with pytest.raises(ConfigError, match="instructions"):
        linked.push(["instructions"])

    assert linked.adapter.vars[linked.adapter.name(linked.agent_id, "instructions")] == (
        2,
        "Remote edit",
    )


def test_push_failed_remote_write_leaves_cursor_and_versions_unchanged(linked: Env) -> None:
    linked.edit_local(instructions="Local instructions")
    linked.adapter.fail_write = ValcoreError("Logfire managed-variable request failed.")
    before = linked.cursor()

    with pytest.raises(ValcoreError, match="request failed"):
        linked.push()

    cursor = linked.cursor()
    assert cursor.generation == before.generation
    assert cursor.instructions_base_text == BASE_INSTR
    assert cursor.instructions_remote_version == 1
    assert linked.state("instructions") == "local_changed"
    assert len(linked.store.list_agent_versions(linked.agent_id)) == 1


@pytest.mark.parametrize("fields", [None, ["instructions"]])
def test_push_retry_after_remote_success_and_cursor_failure_creates_no_duplicate_version(
    linked: Env, monkeypatch: pytest.MonkeyPatch, fields: list[str] | None
) -> None:
    linked.edit_local(instructions="Local instructions")
    real = linked.store.advance_agent_prompt_sync_link
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValcoreError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(linked.store, "advance_agent_prompt_sync_link", flaky)

    with pytest.raises(ValcoreError, match="disk full"):
        linked.push(fields)
    assert linked.remote_version("instructions") == 2
    assert linked.cursor().instructions_remote_version == 1  # cursor did not advance
    assert linked.state("instructions") == "in_sync"  # identical text, stale cursor

    linked.push(fields)  # retry reconciles instead of publishing again

    assert linked.remote_version("instructions") == 2
    assert len(linked.adapter.writes) == 1
    cursor = linked.cursor()
    assert cursor.instructions_remote_version == 2
    assert cursor.instructions_base_text == "Local instructions"
    assert cursor.agent_version_id == linked.active_version().id


def test_push_rechecks_local_head_after_remote_write(linked: Env) -> None:
    linked.edit_local(instructions="Local instructions")
    stale = linked.rev()
    linked.adapter.after_write = lambda: linked.edit_local(template="Raced {question}")

    with pytest.raises(SyncConflictError):
        linked.svc.push(linked.agent_id, None, stale)

    cursor = linked.cursor()
    assert cursor.generation == 0  # cursor advances only after the local-head recheck succeeds
    assert cursor.instructions_remote_version == 1
    assert cursor.instructions_base_text == BASE_INSTR


def test_push_stale_remote_head_raises_sync_conflict_and_keeps_cursor(linked: Env) -> None:
    linked.edit_local(instructions="Local instructions")
    stale = linked.rev()
    linked.set_remote("instructions", "Someone else's edit")  # remote head moved after inspect

    with pytest.raises(SyncConflictError):
        linked.svc.push(linked.agent_id, None, stale)

    assert linked.adapter.writes == []
    assert linked.adapter.vars[linked.adapter.name(linked.agent_id, "instructions")] == (
        2,
        "Someone else's edit",
    )
    assert linked.cursor().generation == 0


def test_push_remote_head_moving_between_reinspect_and_write_is_detected(linked: Env) -> None:
    linked.edit_local(instructions="Local instructions")
    revision = linked.rev()
    linked.adapter.after_read = lambda: linked.set_remote("instructions", "Racing writer")

    with pytest.raises(SyncConflictError):
        linked.svc.push(linked.agent_id, None, revision)

    assert linked.adapter.writes == []
    assert linked.cursor().generation == 0


def test_push_with_stale_revision_after_local_edit_raises(linked: Env) -> None:
    linked.edit_local(instructions="First edit")
    stale = linked.rev()
    linked.edit_local(instructions="Second edit")

    with pytest.raises(SyncConflictError):
        linked.svc.push(linked.agent_id, None, stale)

    assert linked.adapter.writes == []


def test_push_with_changed_key_is_blocked_without_read_or_write(linked: Env) -> None:
    linked.edit_local(instructions="Local instructions")
    linked.key.value = OTHER_FP
    reads = linked.adapter.reads

    with pytest.raises(ValcoreError, match="(?i)unlink"):
        linked.push()

    assert linked.adapter.reads == reads
    assert linked.adapter.writes == []
    assert linked.cursor().generation == 0


def test_push_without_configured_key_is_rejected(linked: Env) -> None:
    linked.edit_local(instructions="Local instructions")
    linked.key.value = None

    with pytest.raises(ConfigError):
        linked.push()

    assert linked.adapter.writes == []


def test_push_when_never_linked_is_rejected(env: Env) -> None:
    with pytest.raises(ValcoreError):
        env.push()

    assert env.adapter.writes == []


def test_push_of_unsupported_local_template_is_rejected(linked: Env) -> None:
    linked.edit_local(template="Answer {question!r}.")

    with pytest.raises(ConfigError, match="input_template"):
        linked.push(["input_template"])

    assert linked.adapter.writes == []


def test_push_after_unlink_and_relink_race_leaves_replacement_link_untouched(linked: Env) -> None:
    """The remote write lands, then Unlink+Link replaces the cursor; Push must not touch it."""
    linked.edit_local(instructions="Local instructions")
    old = linked.cursor()
    revision = linked.rev()

    def unlink_and_relink() -> None:
        replace_link(linked, old)

    linked.adapter.after_write = unlink_and_relink

    with pytest.raises(SyncConflictError):
        linked.svc.push(linked.agent_id, None, revision)

    replacement = linked.cursor()
    assert replacement.id != old.id
    assert replacement.generation == 0
    assert replacement.instructions_base_text == BASE_INSTR
    assert replacement.instructions_remote_version == 1


# -- Resolve -------------------------------------------------------------------------------------


def test_resolve_local_pushes_conflicted_key_and_leaves_others_alone(linked: Env) -> None:
    linked.edit_local(instructions="Local wins", template="Local {question}")
    linked.set_remote("instructions", "Remote loses")
    before = linked.cursor()

    linked.resolve(["instructions"], "local")

    assert linked.adapter.writes == [{"instructions": "Local wins"}]
    assert linked.remote_version("instructions") == 3
    cursor = linked.cursor()
    assert cursor.instructions_base_text == "Local wins"
    assert cursor.instructions_remote_version == 3
    assert cursor.input_template_base_text == before.input_template_base_text
    assert cursor.input_template_remote_version == before.input_template_remote_version
    assert linked.state("instructions") == "in_sync"
    assert linked.state("input_template") == "local_changed"
    assert len(linked.store.list_agent_versions(linked.agent_id)) == 1


def test_resolve_remote_pulls_conflicted_key_into_a_new_version(linked: Env) -> None:
    linked.edit_local(instructions="Local loses")
    linked.set_remote("instructions", "Remote wins")
    source_id = linked.active_version().id

    linked.resolve(["instructions"], "remote")

    new = linked.active_version()
    assert new.id != source_id
    assert new.spec["instructions"] == "Remote wins"
    assert linked.store.get_agent_version(source_id).spec["instructions"] == "Local loses"
    assert linked.adapter.writes == []
    cursor = linked.cursor()
    assert cursor.instructions_base_text == "Remote wins"
    assert cursor.instructions_remote_version == 2
    assert cursor.agent_version_id == new.id
    assert linked.state("instructions") == "in_sync"


def test_resolve_remote_for_input_template_stores_valcore_format(linked: Env) -> None:
    linked.edit_local(template="Local {question}")
    linked.set_remote("input_template", "Remote {{question}} / {{context}}")

    linked.resolve(["input_template"], "remote")

    assert linked.active_version().prompt_template == "Remote {question} / {context}"
    assert linked.cursor().input_template_base_text == "Remote {question} / {context}"


def test_resolve_both_conflicted_keys_in_one_call(linked: Env) -> None:
    linked.edit_local(instructions="Local A", template="Local {question}")
    linked.set_remote("instructions", "Remote A")
    linked.set_remote("input_template", "Remote {{question}}")

    linked.resolve(list(KEYS), "local")

    assert linked.adapter.writes == [
        {"instructions": "Local A", "input_template": "Local {{question}}"}
    ]
    assert {linked.state(key) for key in KEYS} == {"in_sync"}


def test_resolve_local_recreates_a_remotely_deleted_variable(linked: Env) -> None:
    linked.delete_remote("instructions")

    linked.resolve(["instructions"], "local")

    assert linked.adapter.writes == [{"instructions": BASE_INSTR}]
    assert linked.remote_version("instructions") == 1
    cursor = linked.cursor()
    assert cursor.instructions_remote_version == 1
    assert cursor.instructions_base_text == BASE_INSTR
    assert linked.state("instructions") == "in_sync"


def test_resolve_local_recreating_a_deleted_variable_sends_null_expectation(
    linked: Env,
) -> None:
    seen: list[dict] = []
    real_write = linked.adapter.write

    def spy(agent_id: str, changes: dict, expected: dict, **kwargs: Any):
        seen.append(dict(expected))
        return real_write(agent_id, changes, expected, **kwargs)

    linked.adapter.write = spy  # type: ignore[method-assign]
    linked.delete_remote("input_template")

    linked.resolve(["input_template"], "local")

    assert seen == [{"input_template": (None, None)}]


def test_resolve_remote_for_a_missing_variable_is_rejected(linked: Env) -> None:
    linked.delete_remote("instructions")
    before = linked.cursor()

    with pytest.raises(ConfigError, match="instructions"):
        linked.resolve(["instructions"], "remote")

    assert linked.cursor().generation == before.generation
    assert len(linked.store.list_agent_versions(linked.agent_id)) == 1
    assert linked.adapter.writes == []


def test_resolve_remote_with_missing_and_conflicted_keys_is_all_or_nothing(linked: Env) -> None:
    linked.delete_remote("instructions")
    linked.edit_local(template="Local {question}")
    linked.set_remote("input_template", "Remote {{question}}")

    with pytest.raises(ConfigError, match="instructions"):
        linked.resolve(list(KEYS), "remote")

    assert len(linked.store.list_agent_versions(linked.agent_id)) == 1
    assert linked.cursor().generation == 0


@pytest.mark.parametrize("state_setup", ["in_sync", "local_changed", "remote_changed"])
@pytest.mark.parametrize("choice", ["local", "remote"])
def test_resolve_rejects_keys_that_are_not_conflicted_or_missing(
    linked: Env, state_setup: str, choice: str
) -> None:
    if state_setup == "local_changed":
        linked.edit_local(instructions="Local edit")
    elif state_setup == "remote_changed":
        linked.set_remote("instructions", "Remote edit")
    writes = len(linked.adapter.writes)

    with pytest.raises(ConfigError, match="instructions"):
        linked.resolve(["instructions"], choice)

    assert len(linked.adapter.writes) == writes
    assert linked.cursor().generation == 0
    assert len(linked.store.list_agent_versions(linked.agent_id)) == 1


def test_resolve_requires_nonempty_fields_and_valid_choice(linked: Env) -> None:
    linked.edit_local(instructions="Local edit")
    linked.set_remote("instructions", "Remote edit")

    with pytest.raises((ConfigError, ValueError)):
        linked.resolve([], "local")
    with pytest.raises((ConfigError, ValueError)):
        linked.resolve(["instructions"], "newest")

    assert linked.adapter.writes == []
    assert linked.cursor().generation == 0


def test_resolve_with_stale_revision_raises(linked: Env) -> None:
    linked.edit_local(instructions="Local edit")
    linked.set_remote("instructions", "Remote edit")
    stale = linked.rev()
    linked.set_remote("instructions", "Remote edit v2")

    with pytest.raises(SyncConflictError):
        linked.svc.resolve(linked.agent_id, ["instructions"], "local", stale)

    assert linked.adapter.writes == []


def test_resolve_local_with_stale_remote_head_raises_and_keeps_cursor(linked: Env) -> None:
    linked.edit_local(instructions="Local edit")
    linked.set_remote("instructions", "Remote edit")
    revision = linked.rev()
    linked.adapter.after_read = lambda: linked.set_remote("instructions", "Racing writer")

    with pytest.raises(SyncConflictError):
        linked.svc.resolve(linked.agent_id, ["instructions"], "local", revision)

    assert linked.adapter.writes == []
    assert linked.cursor().generation == 0


def test_resolve_local_rejects_key_rotation_before_write(linked: Env) -> None:
    linked.edit_local(instructions="Local edit")
    linked.set_remote("instructions", "Remote edit")
    revision = linked.rev()
    linked.adapter.after_read = lambda: setattr(linked.key, "value", OTHER_FP)

    with pytest.raises(SyncConflictError):
        linked.svc.resolve(linked.agent_id, ["instructions"], "local", revision)

    assert linked.adapter.writes == []
    assert linked.cursor().generation == 0


def test_resolve_with_changed_key_is_blocked(linked: Env) -> None:
    linked.edit_local(instructions="Local edit")
    linked.set_remote("instructions", "Remote edit")
    linked.key.value = OTHER_FP
    reads = linked.adapter.reads

    with pytest.raises(ValcoreError, match="(?i)unlink"):
        linked.resolve(["instructions"], "local")

    assert linked.adapter.reads == reads
    assert linked.adapter.writes == []


# -- Unlink --------------------------------------------------------------------------------------


def test_unlink_deletes_only_the_cursor(linked: Env) -> None:
    linked.edit_local(instructions="Local edit")
    versions = linked.store.list_agent_versions(linked.agent_id)
    remote = dict(linked.adapter.vars)

    linked.unlink()

    assert linked.cursor() is None
    assert linked.store.list_agent_versions(linked.agent_id) == versions
    assert linked.adapter.vars == remote
    assert linked.adapter.writes == []
    status = linked.status()
    assert status.linked is False
    assert status.templates["instructions"].base_text is None


def test_unlink_with_stale_revision_raises_and_keeps_cursor(linked: Env) -> None:
    stale = linked.rev()
    linked.set_remote("instructions", "Remote edit")

    with pytest.raises(SyncConflictError):
        linked.svc.unlink(linked.agent_id, stale)

    assert linked.cursor() is not None


def test_unlink_when_not_linked_is_rejected(env: Env) -> None:
    with pytest.raises(ValcoreError):
        env.unlink()


def test_unlink_remains_available_after_key_change_without_contacting_the_new_project(
    linked: Env,
) -> None:
    linked.key.value = OTHER_FP
    status = linked.status()
    assert status.error
    reads = linked.adapter.reads

    linked.svc.unlink(linked.agent_id, status.revision)

    assert linked.cursor() is None
    assert linked.adapter.reads == reads
    assert linked.adapter.writes == []


def test_key_change_revision_is_valid_only_for_unlink(linked: Env) -> None:
    linked.key.value = OTHER_FP
    revision = linked.rev()

    with pytest.raises(ValcoreError):
        linked.svc.pull(linked.agent_id, None, revision)
    with pytest.raises(ValcoreError):
        linked.svc.push(linked.agent_id, None, revision)
    with pytest.raises(ValcoreError):
        linked.svc.resolve(linked.agent_id, ["instructions"], "local", revision)

    assert linked.cursor() is not None
    assert linked.adapter.writes == []


def test_relink_with_new_key_after_key_change(linked: Env) -> None:
    linked.key.value = OTHER_FP
    linked.unlink()

    linked.link("local")

    cursor = linked.cursor()
    assert cursor.key_fingerprint == OTHER_FP
    assert cursor.generation == 0
    assert {linked.state(key) for key in KEYS} == {"in_sync"}


def test_stale_revision_from_before_unlink_and_relink_is_rejected(linked: Env) -> None:
    linked.edit_local(instructions="Local edit")
    stale = linked.rev()
    old_id = linked.cursor().id
    linked.unlink()
    linked.link("local")
    replacement = linked.cursor()
    assert replacement.id != old_id
    assert replacement.generation == 0

    with pytest.raises(SyncConflictError):
        linked.svc.push(linked.agent_id, None, stale)
    with pytest.raises(SyncConflictError):
        linked.svc.pull(linked.agent_id, None, stale)
    with pytest.raises(SyncConflictError):
        linked.svc.unlink(linked.agent_id, stale)

    after = linked.cursor()
    assert (after.id, after.generation) == (replacement.id, 0)
