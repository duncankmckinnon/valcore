"""Tests for the Logfire managed-variable adapter behind agent prompt sync.

Pins ``PromptVariableAdapter.read`` / ``.write``: the single project-scoped key is resolved once
per call; each call builds an isolated SDK instance via ``logfire.configure(local=True, ...)``
and always shuts it down; only the two stable ``valcore_agent_<id>_...`` variables are read or
written; an absent variable (no latest version) is distinct from a version holding ``""``;
writes only touch the ``valcore_sync`` label, preserve everything else on the variable, use
``mode="merge"``, push only the selected variables, and verify by reading back.

No test makes a live Logfire call. A fake SDK instance stands in for ``logfire.configure``'s
return value, but it stores and returns the *real* ``VariablesConfig`` / ``LabeledValue`` models
so schema drift in the pinned SDK surfaces here.

Assumed public shapes (the task text fixes the method names, not the return types):
``RemoteSnapshot.templates`` maps ``"instructions"`` / ``"input_template"`` to a record with
``variable_name``, ``version`` (``None`` when absent) and ``text`` (``None`` when absent);
``changes`` is ``{key: new_text}``; ``expected_versions`` contains both version and text;
``SyncConflictError`` lives in ``valcore.errors``.
"""

import copy
import json
from types import SimpleNamespace
from typing import Any

import logfire
import pytest
from logfire._internal.config import VariablesOptions
from logfire.variables.config import (
    LabeledValue,
    LabelRef,
    LatestVersion,
    Rollout,
    RolloutOverride,
    VariableConfig,
    VariablesConfig,
)
from logfire.variables.remote import LogfireRemoteVariableProvider

from valcore.config import FileConfig, save_config
from valcore.errors import ConfigError, SyncConflictError, ValcoreError
from valcore.logfire_prompt_variables import PromptVariableAdapter

AGENT_ID = "agt123"
INSTR = f"valcore_agent_{AGENT_ID}_instructions"
TMPL = f"valcore_agent_{AGENT_ID}_input_template"
KEY = "sk-test-secret-key-value"
FAILURES = (ValcoreError, RuntimeError)  # domain error or the SDK failure surfacing as-is


def make_variable(
    name: str,
    text: str | None,
    version: int = 1,
    **extra: Any,
) -> VariableConfig:
    """A remote variable whose latest version holds ``text`` (or no latest version when None)."""
    kwargs: dict[str, Any] = {
        "name": name,
        "labels": {},
        "rollout": Rollout(labels={}),
        "overrides": [],
        "json_schema": {"type": "string"},
    }
    if text is not None:
        kwargs["latest_version"] = LatestVersion(
            version=version, serialized_value=json.dumps(text, ensure_ascii=False)
        )
    kwargs.update(extra)
    return VariableConfig(**kwargs)


class FakeInstance:
    """Stands in for the isolated SDK instance; simulates Logfire's push semantics."""

    def __init__(self, variables: dict[str, VariableConfig]) -> None:
        self.remote = variables
        self.pulls = 0
        self.pushes: list[dict[str, Any]] = []
        self.shutdowns = 0
        self.pull_error: Exception | None = None
        self.push_error: Exception | None = None
        self.push_result = True
        self.drop_writes = False

    def variables_pull_config(self) -> VariablesConfig:
        self.pulls += 1
        if self.pull_error is not None:
            raise self.pull_error
        return VariablesConfig(variables=copy.deepcopy(self.remote))

    def variables_push_config(
        self,
        config: VariablesConfig,
        *,
        mode: str = "merge",
        dry_run: bool = False,
        yes: bool = False,
    ) -> bool:
        self.pushes.append({"config": config, "mode": mode, "yes": yes})
        if self.push_error is not None:
            raise self.push_error
        if not self.drop_writes:
            for name, var in config.variables.items():
                new = copy.deepcopy(var)
                synced = var.labels.get("valcore_sync")
                if isinstance(synced, LabeledValue):
                    new.latest_version = LatestVersion(
                        version=synced.version, serialized_value=synced.serialized_value
                    )
                self.remote[name] = new
        return self.push_result

    def shutdown(self) -> None:
        self.shutdowns += 1


class Harness:
    """Records ``logfire.configure`` calls and hands out one fake instance per call."""

    def __init__(self, remote: dict[str, VariableConfig]) -> None:
        self.remote = remote
        self.instances: list[FakeInstance] = []
        self.configure_calls: list[dict[str, Any]] = []
        self.configure_error: Exception | None = None
        self.customize: Any = None

    def configure(self, **kwargs: Any) -> FakeInstance:
        self.configure_calls.append(kwargs)
        if self.configure_error is not None:
            raise self.configure_error
        inst = FakeInstance(self.remote)
        if self.customize:
            self.customize(inst)
        self.instances.append(inst)
        return inst


@pytest.fixture
def remote() -> dict[str, VariableConfig]:
    return {}


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, remote: dict[str, VariableConfig]) -> Harness:
    save_config(FileConfig(logfire_write_key=KEY))
    h = Harness(remote)
    monkeypatch.setattr(logfire, "configure", h.configure)
    return h


def label_value(var: VariableConfig, label: str = "valcore_sync") -> LabeledValue:
    entry = var.labels[label]
    assert isinstance(entry, LabeledValue)
    return entry


def expected(
    remote: dict[str, VariableConfig], versions: dict[str, int | None]
) -> dict[str, tuple[int | None, str | None]]:
    """Build an observed head for tests where only the version is in dispute."""
    names = {"instructions": INSTR, "input_template": TMPL}
    result = {}
    for key, version in versions.items():
        variable = remote.get(names[key])
        latest = variable.latest_version if variable is not None else None
        result[key] = (version, json.loads(latest.serialized_value) if latest else None)
    return result


def test_configured_key_rotation_is_rejected_before_sdk_request(harness):
    adapter = PromptVariableAdapter()
    inspected_fingerprint = adapter.key_fingerprint()
    assert inspected_fingerprint and KEY not in inspected_fingerprint
    save_config(FileConfig(logfire_write_key="sk-another-project"))

    with pytest.raises(SyncConflictError):
        adapter.read(AGENT_ID, expected_fingerprint=inspected_fingerprint)
    with pytest.raises(SyncConflictError):
        adapter.write(
            AGENT_ID,
            {"instructions": "new"},
            {"instructions": (None, None)},
            expected_fingerprint=inspected_fingerprint,
        )

    assert harness.configure_calls == []


# --------------------------------------------------------------------------- read


def test_read_returns_latest_version_and_decoded_text(harness, remote):
    remote[INSTR] = make_variable(INSTR, "Be terse.", version=3)
    remote[TMPL] = make_variable(TMPL, "Q: {{question}}", version=2)

    snap = PromptVariableAdapter().read(AGENT_ID)

    assert snap.templates["instructions"].variable_name == INSTR
    assert snap.templates["instructions"].version == 3
    assert snap.templates["instructions"].text == "Be terse."
    assert snap.templates["input_template"].variable_name == TMPL
    assert snap.templates["input_template"].version == 2
    assert snap.templates["input_template"].text == "Q: {{question}}"


def test_read_decodes_json_escapes_and_unicode(harness, remote):
    text = 'Say "héllo"\nline two \\ backslash'
    remote[INSTR] = make_variable(INSTR, text)
    remote[TMPL] = make_variable(TMPL, "")

    snap = PromptVariableAdapter().read(AGENT_ID)

    assert snap.templates["instructions"].text == text


def test_read_absent_variable_is_none_not_empty(harness, remote):
    # INSTR exists with no latest version; TMPL doesn't exist at all.
    remote[INSTR] = make_variable(INSTR, None)

    snap = PromptVariableAdapter().read(AGENT_ID)

    for key, name in (("instructions", INSTR), ("input_template", TMPL)):
        assert snap.templates[key].variable_name == name
        assert snap.templates[key].version is None
        assert snap.templates[key].text is None


def test_read_empty_string_version_is_distinct_from_absent(harness, remote):
    remote[INSTR] = make_variable(INSTR, "", version=4)

    snap = PromptVariableAdapter().read(AGENT_ID)

    assert snap.templates["instructions"].text == ""
    assert snap.templates["instructions"].version == 4
    assert snap.templates["input_template"].text is None


def test_read_ignores_unrelated_and_other_agents_variables(harness, remote):
    remote["feature_flag"] = make_variable("feature_flag", "on")
    other = "valcore_agent_other_instructions"
    remote[other] = make_variable(other, "not mine")
    remote[INSTR] = make_variable(INSTR, "mine")

    snap = PromptVariableAdapter().read(AGENT_ID)

    assert snap.templates["instructions"].text == "mine"
    assert snap.templates["input_template"].text is None
    assert set(snap.templates) == {"instructions", "input_template"}


def test_read_rejects_non_string_stored_value(harness, remote):
    remote[INSTR] = make_variable(INSTR, "x")
    remote[INSTR].latest_version = LatestVersion(version=1, serialized_value=json.dumps({"a": 1}))

    with pytest.raises(ConfigError, match="string"):
        PromptVariableAdapter().read(AGENT_ID)


def test_read_never_writes(harness, remote):
    remote[INSTR] = make_variable(INSTR, "x")

    PromptVariableAdapter().read(AGENT_ID)

    assert harness.instances[0].pushes == []


# ------------------------------------------------------- isolation and lifecycle


def test_configure_is_isolated_and_uses_only_the_api_key(harness, remote):
    PromptVariableAdapter().read(AGENT_ID)

    assert harness.configure_calls == [
        {"local": True, "send_to_logfire": False, "console": False, "api_key": KEY}
    ]


def test_uses_a_fresh_instance_per_call(harness, remote):
    adapter = PromptVariableAdapter()
    adapter.read(AGENT_ID)
    adapter.read(AGENT_ID)

    assert len(harness.instances) == 2
    assert harness.instances[0] is not harness.instances[1]


def test_never_reconfigures_process_global_logfire(harness, remote, monkeypatch):
    remote[INSTR] = make_variable(INSTR, "a")
    remote[TMPL] = make_variable(TMPL, "b")
    for attr in ("shutdown", "force_flush"):
        if hasattr(logfire, attr):
            monkeypatch.setattr(
                logfire,
                attr,
                lambda *a, _attr=attr, **k: pytest.fail(f"global logfire.{_attr} called"),
            )

    adapter = PromptVariableAdapter()
    adapter.read(AGENT_ID)
    adapter.write(AGENT_ID, {"instructions": "c"}, expected(remote, {"instructions": 1}))

    # Only the per-call isolated configure (local=True) ever ran.
    assert harness.configure_calls
    assert all(call["local"] is True for call in harness.configure_calls)
    assert all("token" not in call for call in harness.configure_calls)


def test_shutdown_after_successful_read(harness, remote):
    PromptVariableAdapter().read(AGENT_ID)

    assert [i.shutdowns for i in harness.instances] == [1]


def test_shutdown_after_failed_read(harness, remote):
    harness.customize = lambda inst: setattr(inst, "pull_error", RuntimeError("boom"))

    with pytest.raises(FAILURES):
        PromptVariableAdapter().read(AGENT_ID)

    assert [i.shutdowns for i in harness.instances] == [1]


def test_shutdown_after_successful_write(harness, remote):
    PromptVariableAdapter().write(
        AGENT_ID, {"instructions": "hi"}, expected(remote, {"instructions": None})
    )

    assert [i.shutdowns for i in harness.instances] == [1]


def test_shutdown_after_failed_write(harness, remote):
    harness.customize = lambda inst: setattr(inst, "push_error", RuntimeError("boom"))

    with pytest.raises(FAILURES):
        PromptVariableAdapter().write(
            AGENT_ID, {"instructions": "hi"}, expected(remote, {"instructions": None})
        )

    assert [i.shutdowns for i in harness.instances] == [1]


def test_shutdown_after_stale_write_rejection(harness, remote):
    remote[INSTR] = make_variable(INSTR, "a", version=2)

    with pytest.raises(SyncConflictError):
        PromptVariableAdapter().write(
            AGENT_ID, {"instructions": "b"}, expected(remote, {"instructions": 1})
        )

    assert [i.shutdowns for i in harness.instances] == [1]


# ------------------------------------------------------------------ key handling


def test_key_resolved_once_per_call(harness, remote, monkeypatch):
    from valcore import config

    calls: list[int] = []
    real = config.resolve_logfire_write_key

    def counting(cfg: FileConfig) -> str | None:
        calls.append(1)
        return real(cfg)

    monkeypatch.setattr(config, "resolve_logfire_write_key", counting)

    PromptVariableAdapter().write(
        AGENT_ID, {"instructions": "x"}, expected(remote, {"instructions": None})
    )

    assert len(calls) == 1


def test_missing_key_raises_config_error_naming_both_scopes(harness, remote):
    save_config(FileConfig())

    for call in (
        lambda: PromptVariableAdapter().read(AGENT_ID),
        lambda: PromptVariableAdapter().write(
            AGENT_ID, {"instructions": "x"}, expected(remote, {"instructions": None})
        ),
    ):
        with pytest.raises(ConfigError) as exc:
            call()
        assert "project:read_variables" in str(exc.value)
        assert "project:write_variables" in str(exc.value)
    assert harness.configure_calls == []


@pytest.mark.parametrize("status", ["401 Unauthorized", "403 Forbidden"])
def test_read_scope_failure_becomes_config_error_without_key(harness, remote, status):
    harness.customize = lambda inst: setattr(
        inst, "pull_error", RuntimeError(f"HTTP {status} {KEY}")
    )

    with pytest.raises(ConfigError) as exc:
        PromptVariableAdapter().read(AGENT_ID)

    assert "project:read_variables" in str(exc.value)
    assert "project:write_variables" in str(exc.value)
    assert KEY not in str(exc.value)
    assert KEY not in repr(exc.value)
    assert [i.shutdowns for i in harness.instances] == [1]


@pytest.mark.parametrize("operation", ["read", "write"])
@pytest.mark.parametrize("has_cache", [False, True])
def test_real_provider_suppressed_fetch_failure_fails_closed(
    harness, monkeypatch, operation, has_cache
):
    """The pinned remote provider swallows failed fetches and returns its cache."""
    provider = LogfireRemoteVariableProvider("https://example.invalid", KEY, VariablesOptions())
    if has_cache:
        provider._config = VariablesConfig(variables={INSTR: make_variable(INSTR, "cached")})
    monkeypatch.setattr(
        provider._session, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("403"))
    )
    monkeypatch.setattr(provider, "_log_error", lambda *a: None)

    class RealProviderInstance:
        config = SimpleNamespace(get_variable_provider=lambda: provider)

        def variables_pull_config(self):
            return provider.pull_config()

        def shutdown(self):
            provider.shutdown()

    monkeypatch.setattr(logfire, "configure", lambda **kwargs: RealProviderInstance())
    with pytest.raises(ConfigError, match="project:read_variables"):
        if operation == "read":
            PromptVariableAdapter().read(AGENT_ID)
        else:
            PromptVariableAdapter().write(
                AGENT_ID, {"instructions": "new"}, {"instructions": (1, "cached")}
            )


def test_write_scope_failure_becomes_config_error_without_key(harness, remote):
    harness.customize = lambda inst: setattr(
        inst, "push_error", RuntimeError(f"HTTP 403 Forbidden {KEY}")
    )

    with pytest.raises(ConfigError) as exc:
        PromptVariableAdapter().write(
            AGENT_ID, {"instructions": "x"}, expected(remote, {"instructions": None})
        )

    assert "project:write_variables" in str(exc.value)
    assert KEY not in str(exc.value)
    assert KEY not in repr(exc.value)


def test_adapter_repr_does_not_expose_key(harness):
    assert KEY not in repr(PromptVariableAdapter())


# ------------------------------------------------------------------------- write


def test_first_push_creates_variables_at_version_one(harness, remote):
    snap = PromptVariableAdapter().write(
        AGENT_ID,
        {"instructions": "Be terse.", "input_template": "Q: {{question}}"},
        expected(remote, {"instructions": None, "input_template": None}),
    )

    push = harness.instances[0].pushes[0]
    assert push["mode"] == "merge"
    assert push["yes"] is True
    assert set(push["config"].variables) == {INSTR, TMPL}
    for name, text in ((INSTR, "Be terse."), (TMPL, "Q: {{question}}")):
        var = push["config"].variables[name]
        assert var.name == name
        assert var.json_schema == {"type": "string"}
        assert var.rollout.labels == {}
        assert var.overrides == []
        assert var.latest_version is None
        assert set(var.labels) == {"valcore_sync"}
        assert label_value(var).version == 1
        assert label_value(var).serialized_value == json.dumps(text)
    assert snap.templates["instructions"].version == 1
    assert snap.templates["instructions"].text == "Be terse."
    assert snap.templates["input_template"].version == 1


def test_first_push_of_empty_string_is_a_real_version(harness, remote):
    snap = PromptVariableAdapter().write(
        AGENT_ID, {"input_template": ""}, expected(remote, {"input_template": None})
    )

    var = harness.instances[0].pushes[0]["config"].variables[TMPL]
    assert label_value(var).serialized_value == '""'
    assert snap.templates["input_template"].version == 1
    assert snap.templates["input_template"].text == ""


def test_serialized_value_is_json_string_with_unicode_preserved(harness, remote):
    text = 'héllo "quoted"\nnew line — 日本語'

    PromptVariableAdapter().write(
        AGENT_ID, {"instructions": text}, expected(remote, {"instructions": None})
    )

    raw = label_value(harness.instances[0].pushes[0]["config"].variables[INSTR]).serialized_value
    assert raw == json.dumps(text, ensure_ascii=False)
    assert "\\u" not in raw
    assert json.loads(raw) == text


def test_changed_push_bumps_from_latest_version(harness, remote):
    remote[INSTR] = make_variable(INSTR, "old", version=4)
    remote[TMPL] = make_variable(TMPL, "t", version=1)

    snap = PromptVariableAdapter().write(
        AGENT_ID, {"instructions": "new"}, expected(remote, {"instructions": 4})
    )

    var = harness.instances[0].pushes[0]["config"].variables[INSTR]
    assert label_value(var).version == 5
    assert label_value(var).serialized_value == '"new"'
    assert snap.templates["instructions"].version == 5
    assert snap.templates["instructions"].text == "new"
    assert snap.templates["input_template"].version == 1
    assert snap.templates["input_template"].text == "t"


def test_push_wraps_only_selected_variables(harness, remote):
    remote[INSTR] = make_variable(INSTR, "old", version=1)
    remote[TMPL] = make_variable(TMPL, "t", version=1)
    remote["unrelated"] = make_variable("unrelated", "x")

    PromptVariableAdapter().write(
        AGENT_ID, {"instructions": "new"}, expected(remote, {"instructions": 1})
    )

    pushed = harness.instances[0].pushes[0]["config"]
    assert set(pushed.variables) == {INSTR}
    assert harness.instances[0].pushes[0]["mode"] == "merge"


def test_push_preserves_existing_config_and_other_labels(harness, remote):
    remote[INSTR] = make_variable(
        INSTR,
        "old",
        version=2,
        description="Judge instructions",
        aliases=["alias_one"],
        example='"e.g."',
        type_name="str",
        labels={
            "production": LabeledValue(version=1, serialized_value='"prod text"'),
            "canary": LabeledValue(version=2, serialized_value='"canary text"'),
        },
        rollout=Rollout(labels={"production": 0.9, "canary": 0.1}),
    )
    before = copy.deepcopy(remote[INSTR])

    PromptVariableAdapter().write(
        AGENT_ID, {"instructions": "new"}, expected(remote, {"instructions": 2})
    )

    var = harness.instances[0].pushes[0]["config"].variables[INSTR]
    assert var.description == "Judge instructions"
    assert var.aliases == ["alias_one"]
    assert var.example == '"e.g."'
    assert var.type_name == "str"
    assert var.json_schema == {"type": "string"}
    assert var.rollout == before.rollout
    assert var.overrides == before.overrides
    assert var.labels["production"] == before.labels["production"]
    assert var.labels["canary"] == before.labels["canary"]
    assert label_value(var).version == 3
    assert set(var.labels) == {"production", "canary", "valcore_sync"}
    # Never publishes: the read-side latest_version is not what we set to promote.
    assert var.latest_version == before.latest_version or var.latest_version is None
    # The pulled remote object was deep-copied, not mutated in place.
    assert "valcore_sync" not in before.labels


def test_push_replaces_only_the_valcore_sync_label(harness, remote):
    remote[INSTR] = make_variable(
        INSTR,
        "old",
        version=2,
        labels={"valcore_sync": LabeledValue(version=2, serialized_value='"old"')},
    )

    PromptVariableAdapter().write(
        AGENT_ID, {"instructions": "new"}, expected(remote, {"instructions": 2})
    )

    var = harness.instances[0].pushes[0]["config"].variables[INSTR]
    assert label_value(var).version == 3
    assert label_value(var).serialized_value == '"new"'


def test_write_never_uses_replace_mode_or_prompt_names(harness, remote):
    PromptVariableAdapter().write(
        AGENT_ID,
        {"instructions": "a", "input_template": "b"},
        expected(remote, {"instructions": None, "input_template": None}),
    )

    for push in harness.instances[0].pushes:
        assert push["mode"] == "merge"
        assert not any(n.startswith("prompt__") for n in push["config"].variables)


def test_unchanged_push_writes_nothing(harness, remote):
    remote[INSTR] = make_variable(INSTR, "same", version=3)
    remote[TMPL] = make_variable(TMPL, "t", version=1)

    snap = PromptVariableAdapter().write(AGENT_ID, {}, expected(remote, {}))

    assert harness.instances[0].pushes == []
    assert snap.templates["instructions"].version == 3
    assert snap.templates["instructions"].text == "same"


def test_write_with_identical_text_does_not_create_a_version(harness, remote):
    remote[INSTR] = make_variable(INSTR, "same", version=3)

    snap = PromptVariableAdapter().write(
        AGENT_ID, {"instructions": "same"}, expected(remote, {"instructions": 3})
    )

    assert harness.instances[0].pushes == []
    assert snap.templates["instructions"].version == 3


def test_only_requested_key_is_written(harness, remote):
    remote[INSTR] = make_variable(INSTR, "i", version=1)
    remote[TMPL] = make_variable(TMPL, "t", version=1)

    PromptVariableAdapter().write(
        AGENT_ID, {"input_template": "t2"}, expected(remote, {"input_template": 1})
    )

    assert set(harness.instances[0].pushes[0]["config"].variables) == {TMPL}
    assert remote[INSTR].latest_version.version == 1


# -------------------------------------------------------------- stale detection


def test_stale_remote_head_is_rejected_before_any_push(harness, remote):
    remote[INSTR] = make_variable(INSTR, "edited in logfire", version=6)

    with pytest.raises(SyncConflictError):
        PromptVariableAdapter().write(
            AGENT_ID, {"instructions": "mine"}, expected(remote, {"instructions": 5})
        )

    assert harness.instances[0].pushes == []


def test_same_version_different_text_is_stale(harness, remote):
    remote[INSTR] = make_variable(INSTR, "edited", version=6)

    with pytest.raises(SyncConflictError):
        PromptVariableAdapter().write(
            AGENT_ID, {"instructions": "mine"}, {"instructions": (6, "old")}
        )

    assert harness.instances[0].pushes == []


def test_expected_absent_but_remote_now_exists_is_stale(harness, remote):
    remote[INSTR] = make_variable(INSTR, "someone created it", version=1)

    with pytest.raises(SyncConflictError):
        PromptVariableAdapter().write(
            AGENT_ID, {"instructions": "mine"}, expected(remote, {"instructions": None})
        )

    assert harness.instances[0].pushes == []


def test_expected_version_but_remote_deleted_is_stale(harness, remote):
    with pytest.raises(SyncConflictError):
        PromptVariableAdapter().write(
            AGENT_ID, {"instructions": "mine"}, expected(remote, {"instructions": 2})
        )

    assert harness.instances[0].pushes == []


def test_stale_check_covers_every_selected_key(harness, remote):
    remote[INSTR] = make_variable(INSTR, "i", version=1)
    remote[TMPL] = make_variable(TMPL, "t", version=9)

    with pytest.raises(SyncConflictError):
        PromptVariableAdapter().write(
            AGENT_ID,
            {"instructions": "i2", "input_template": "t2"},
            expected(remote, {"instructions": 1, "input_template": 8}),
        )

    # No partial write even though instructions matched.
    assert harness.instances[0].pushes == []


# ---------------------------------------------- unexpected schema/label collisions


def test_rejects_non_string_schema_before_writing(harness, remote):
    remote[INSTR] = make_variable(INSTR, "old", version=1, json_schema={"type": "integer"})

    with pytest.raises(ConfigError):
        PromptVariableAdapter().write(
            AGENT_ID, {"instructions": "new"}, expected(remote, {"instructions": 1})
        )

    assert harness.instances[0].pushes == []


def test_rejects_valcore_sync_label_ref_collision_before_writing(harness, remote):
    from logfire.variables.config import LabelRef

    remote[INSTR] = make_variable(
        INSTR, "old", version=1, labels={"valcore_sync": LabelRef(ref="latest")}
    )

    with pytest.raises(ConfigError):
        PromptVariableAdapter().write(
            AGENT_ID, {"instructions": "new"}, expected(remote, {"instructions": 1})
        )

    assert harness.instances[0].pushes == []


@pytest.mark.parametrize(
    "labels,rollout",
    [
        (
            {
                "production": LabelRef(ref="valcore_sync"),
                "valcore_sync": LabeledValue(version=1, serialized_value='"old"'),
            },
            Rollout(labels={}),
        ),
        ({"production": LabelRef(ref="latest")}, Rollout(labels={})),
        (
            {
                "production": LabelRef(ref="canary"),
                "canary": LabelRef(ref="valcore_sync"),
                "valcore_sync": LabeledValue(version=1, serialized_value='"old"'),
            },
            Rollout(labels={}),
        ),
        (
            {"valcore_sync": LabeledValue(version=1, serialized_value='"old"')},
            Rollout(labels={"valcore_sync": 1.0}),
        ),
    ],
)
def test_rejects_serving_references_before_write(harness, remote, labels, rollout):
    remote[INSTR] = make_variable(INSTR, "old", version=1, labels=labels, rollout=rollout)

    with pytest.raises(ConfigError):
        PromptVariableAdapter().write(
            AGENT_ID, {"instructions": "new"}, {"instructions": (1, "old")}
        )

    assert harness.instances[0].pushes == []


def test_rejects_override_rollout_selecting_sync_label(harness, remote):
    remote[INSTR] = make_variable(
        INSTR,
        "old",
        labels={"valcore_sync": LabeledValue(version=1, serialized_value='"old"')},
        overrides=[RolloutOverride(conditions=[], rollout=Rollout(labels={"valcore_sync": 1.0}))],
    )

    with pytest.raises(ConfigError):
        PromptVariableAdapter().write(
            AGENT_ID, {"instructions": "new"}, {"instructions": (1, "old")}
        )

    assert harness.instances[0].pushes == []


def test_rejects_unknown_template_key(harness, remote):
    with pytest.raises(ValueError):
        PromptVariableAdapter().write(AGENT_ID, {"prompt__evil": "x"}, expected(remote, {}))

    assert harness.instances == [] or harness.instances[0].pushes == []


@pytest.mark.parametrize("bad_id", ["", "a b", "x-y", "a/b", "../x", "id\n"])
def test_rejects_agent_id_that_cannot_form_a_variable_name(harness, remote, bad_id):
    with pytest.raises(ValueError):
        PromptVariableAdapter().read(bad_id)

    assert harness.instances == [] or all(i.pushes == [] for i in harness.instances)


# ------------------------------------------------------------ read-back / errors


def test_returns_read_back_snapshot_not_the_request(harness, remote):
    snap = PromptVariableAdapter().write(
        AGENT_ID, {"instructions": "hello"}, expected(remote, {"instructions": None})
    )

    inst = harness.instances[0]
    assert inst.pulls == 2  # stale check, then read-back
    assert snap.templates["instructions"].text == remote[
        INSTR
    ].latest_version.serialized_value.strip('"')


def test_false_push_result_without_matching_readback_is_error(harness, remote):
    def customize(inst: FakeInstance) -> None:
        inst.push_result = False
        inst.drop_writes = True

    harness.customize = customize

    with pytest.raises(FAILURES) as exc:
        PromptVariableAdapter().write(
            AGENT_ID, {"instructions": "x"}, expected(remote, {"instructions": None})
        )

    assert not isinstance(exc.value, SyncConflictError)


def test_readback_mismatch_is_error_even_when_push_returns_true(harness, remote):
    harness.customize = lambda inst: setattr(inst, "drop_writes", True)

    with pytest.raises(FAILURES):
        PromptVariableAdapter().write(
            AGENT_ID, {"instructions": "x"}, expected(remote, {"instructions": None})
        )


def test_false_push_result_with_matching_readback_is_accepted(harness, remote):
    harness.customize = lambda inst: setattr(inst, "push_result", False)

    snap = PromptVariableAdapter().write(
        AGENT_ID, {"instructions": "x"}, expected(remote, {"instructions": None})
    )

    assert snap.templates["instructions"].text == "x"
    assert snap.templates["instructions"].version == 1


def test_partial_failure_on_second_write_leaves_first_visible_for_retry(harness, remote):
    """A two-variable write is not atomic; a retry sees the first write and must not duplicate."""
    adapter = PromptVariableAdapter()

    def fail_after_first(inst: FakeInstance) -> None:
        def partial_push(config: VariablesConfig, *, mode: str, yes: bool) -> bool:
            assert list(config.variables) == [INSTR, TMPL]
            first = copy.deepcopy(config.variables[INSTR])
            sync = label_value(first)
            first.latest_version = LatestVersion(
                version=sync.version, serialized_value=sync.serialized_value
            )
            remote[INSTR] = first
            raise RuntimeError("second variable failed")

        inst.variables_push_config = partial_push  # type: ignore[method-assign]

    harness.customize = fail_after_first
    with pytest.raises(FAILURES):
        adapter.write(
            AGENT_ID,
            {"instructions": "i", "input_template": "t"},
            expected(remote, {"instructions": None, "input_template": None}),
        )

    harness.customize = None
    snap = adapter.read(AGENT_ID)
    assert snap.templates["instructions"].version == 1
    assert snap.templates["instructions"].text == "i"
    assert snap.templates["input_template"].version is None

    retry = adapter.write(AGENT_ID, {"input_template": "t"}, {"input_template": (None, None)})
    assert retry.templates["instructions"].version == 1
    assert retry.templates["input_template"].version == 1
    assert set(harness.instances[-1].pushes[0]["config"].variables) == {TMPL}


# ----------------------------------------------------- real SDK schema round-trip


def test_real_sdk_models_serialize_the_pushed_shape():
    """Guards against schema drift in the pinned SDK: the shape we build must round-trip."""
    var = VariableConfig(
        name=INSTR,
        labels={"valcore_sync": LabeledValue(version=1, serialized_value=json.dumps("x"))},
        rollout=Rollout(labels={}),
        overrides=[],
        json_schema={"type": "string"},
    )
    config = VariablesConfig(variables={INSTR: var})

    dumped = config.model_dump(mode="json", exclude_none=True)
    restored = VariablesConfig.model_validate(dumped)

    assert restored == config
    assert dumped["variables"][INSTR]["labels"]["valcore_sync"] == {
        "version": 1,
        "serialized_value": '"x"',
    }
    assert restored.variables[INSTR].json_schema == {"type": "string"}


# ------------------------------------------------------------- extra edge cases


def test_read_rejects_non_json_stored_value(harness, remote):
    remote[INSTR] = make_variable(INSTR, "x")
    remote[INSTR].latest_version = LatestVersion(version=1, serialized_value="not json{")

    with pytest.raises(ConfigError, match="JSON string"):
        PromptVariableAdapter().read(AGENT_ID)
    assert [i.shutdowns for i in harness.instances] == [1]


def test_configure_failure_is_sanitized(harness, remote):
    harness.configure_error = RuntimeError(f"boom {KEY}")

    with pytest.raises(ValcoreError) as exc:
        PromptVariableAdapter().read(AGENT_ID)

    assert KEY not in str(exc.value)


def test_write_requires_expected_versions_for_every_change(harness, remote):
    with pytest.raises(ValueError):
        PromptVariableAdapter().write(AGENT_ID, {"instructions": "x"}, expected(remote, {}))


def test_write_rejects_non_string_value(harness, remote):
    with pytest.raises(ValueError):
        PromptVariableAdapter().write(
            AGENT_ID, {"instructions": 5}, expected(remote, {"instructions": None})
        )  # type: ignore[dict-item]


def test_rejects_sync_label_ahead_of_latest(harness, remote):
    remote[INSTR] = make_variable(
        INSTR,
        "old",
        version=1,
        labels={"valcore_sync": LabeledValue(version=7, serialized_value='"x"')},
    )

    with pytest.raises(ConfigError):
        PromptVariableAdapter().write(
            AGENT_ID, {"instructions": "new"}, expected(remote, {"instructions": 1})
        )

    assert harness.instances[0].pushes == []


def test_rejects_sync_label_diverging_from_latest_text(harness, remote):
    remote[INSTR] = make_variable(
        INSTR,
        "old",
        version=1,
        labels={"valcore_sync": LabeledValue(version=1, serialized_value='"different"')},
    )

    with pytest.raises(ConfigError):
        PromptVariableAdapter().write(
            AGENT_ID, {"instructions": "new"}, expected(remote, {"instructions": 1})
        )

    assert harness.instances[0].pushes == []
