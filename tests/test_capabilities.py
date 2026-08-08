"""Tests for the shared capability registry and scalar-type map.

The registry is the single home for the harness capabilities that three modules used to
describe independently (``models.VALID_CAPABILITIES``, ``factory._CAPABILITY_MODULES``,
``export._CAPABILITY_IMPORTS``). These tests pin the registry's contents and, more
importantly, guard against the triplication returning: a capability added in one place must
become visible to every consumer with no other edit.
"""

import dataclasses
import importlib

import pytest

from valcore import capabilities, export, models
from valcore.capabilities import (
    CAPABILITY_REGISTRY,
    VALID_CAPABILITIES,
    CapabilityEntry,
)
from valcore.errors import ConfigError
from valcore.export import render_script
from valcore.factory import build_capabilities
from valcore.models import (
    SCALAR_TYPES,
    CapabilitySpec,
    EvaluatorVersion,
    FieldType,
    ScoreKind,
)

EXPECTED_CAPABILITY_NAMES = {"CodeMode", "SubAgents", "Planning", "FileSystem", "Shell"}


def _make_version(**overrides: object) -> EvaluatorVersion:
    """Build a minimal categorical EvaluatorVersion, overridable per test."""
    base: dict[str, object] = {
        "evaluator_id": "e1",
        "version_name": "v1",
        "model": "gateway/anthropic:claude-sonnet-5",
        "instructions": "Judge the row.",
        "prompt_template": "Input: {text}",
        "score_field": "verdict",
        "score_kind": ScoreKind.CATEGORICAL,
        "score_labels": ["good", "bad"],
        "required_columns": ["text"],
        "output_fields": [
            {
                "name": "verdict",
                "type": "enum",
                "description": "the call",
                "enum_values": ["good", "bad"],
            },
        ],
        "tools": [],
        "capabilities": [],
    }
    base.update(overrides)
    return EvaluatorVersion(**base)


# --- CapabilityEntry ----------------------------------------------------------


def test_capability_entry_is_a_frozen_dataclass() -> None:
    """CapabilityEntry is a frozen dataclass carrying a module and a class name."""
    entry = CapabilityEntry(module="pkg.mod", class_name="Thing")
    assert entry.module == "pkg.mod"
    assert entry.class_name == "Thing"
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.module = "other"  # type: ignore[misc]


# --- registry contents --------------------------------------------------------


def test_registry_holds_exactly_the_five_harness_capabilities() -> None:
    """The registry names precisely the five known harness capabilities."""
    assert set(CAPABILITY_REGISTRY) == EXPECTED_CAPABILITY_NAMES


@pytest.mark.parametrize("name", sorted(EXPECTED_CAPABILITY_NAMES))
def test_registry_entry_class_name_matches_its_key(name: str) -> None:
    """Each entry's class name is its registry key, so the two never drift apart."""
    assert CAPABILITY_REGISTRY[name].class_name == name


@pytest.mark.parametrize("name", sorted(EXPECTED_CAPABILITY_NAMES))
def test_every_registry_entry_imports_its_class(name: str) -> None:
    """A bad module path or class name fails here rather than at agent-build time."""
    entry = CAPABILITY_REGISTRY[name]
    module = importlib.import_module(entry.module)
    cls = getattr(module, entry.class_name)
    assert cls.__name__ == entry.class_name


def test_codemode_module_is_the_deep_runtime_path() -> None:
    """CodeMode resolves to the deep module the live runtime uses, not the package root.

    ``factory`` and ``export`` disagreed on this before consolidation; the deep path wins
    because that is what ``build_capabilities`` imports at run time.
    """
    assert CAPABILITY_REGISTRY["CodeMode"].module == "pydantic_ai_harness.code_mode"


# The exact ``from <module> import <class>`` line each capability emitted before the
# refactor. ``render_script`` output is contractually byte-identical (locked decision #6),
# and CodeMode/FileSystem/Shell historically rendered from the ``pydantic_ai_harness`` root
# even though the live runtime imports them from a deep module. ``test_export.py`` only pins
# CodeMode, so without this table a dropped ``render_module`` would silently repoint the
# other two to their deep paths with no failing test.
_ORIGINAL_RENDER_IMPORTS: dict[str, str] = {
    "CodeMode": "from pydantic_ai_harness import CodeMode",
    "FileSystem": "from pydantic_ai_harness import FileSystem",
    "Shell": "from pydantic_ai_harness import Shell",
    "SubAgents": "from pydantic_ai_harness.subagents import SubAgents",
    "Planning": "from pydantic_ai_harness.planning import Planning",
}


@pytest.mark.parametrize("name", sorted(EXPECTED_CAPABILITY_NAMES))
def test_render_script_import_line_is_byte_identical_per_capability(name: str) -> None:
    """Every capability renders the same import line it did before consolidation.

    ``script_module`` (``render_module`` falling back to ``module``) is what preserves the
    root re-export path for the three capabilities that used it, keeping ``render_script``
    byte-identical while the runtime still imports the deep module.
    """
    entry = CAPABILITY_REGISTRY[name]
    rendered = f"from {entry.script_module} import {entry.class_name}"
    assert rendered == _ORIGINAL_RENDER_IMPORTS[name]

    src = render_script(_make_version(capabilities=[{"name": name, "config": {}}]))
    assert _ORIGINAL_RENDER_IMPORTS[name] in src


# --- VALID_CAPABILITIES -------------------------------------------------------


def test_valid_capabilities_equals_registry_key_set() -> None:
    """VALID_CAPABILITIES is exactly the registry's keys, as a frozenset."""
    assert isinstance(VALID_CAPABILITIES, frozenset)
    assert VALID_CAPABILITIES == frozenset(CAPABILITY_REGISTRY)


def test_models_re_exports_the_registry_valid_capabilities() -> None:
    """``models.VALID_CAPABILITIES`` is the registry's set, not an independent literal."""
    assert models.VALID_CAPABILITIES is capabilities.VALID_CAPABILITIES
    assert set(models.VALID_CAPABILITIES) == set(CAPABILITY_REGISTRY)


# --- CapabilitySpec is driven by the registry ---------------------------------


@pytest.mark.parametrize("name", sorted(EXPECTED_CAPABILITY_NAMES))
def test_capability_spec_accepts_every_registered_name(name: str) -> None:
    """CapabilitySpec validation admits exactly the registry's names."""
    assert CapabilitySpec(name=name).name == name


def test_capability_spec_rejects_a_name_absent_from_the_registry() -> None:
    """An unregistered capability name is rejected with the standing message."""
    with pytest.raises(ValueError, match="Unknown capability"):
        CapabilitySpec(name="Telepathy")


# --- the consolidation guard --------------------------------------------------


def test_registry_addition_is_visible_to_export_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    """A capability added to the registry is rendered by export with no edit to export.py.

    Proves ``export`` reads the shared registry rather than a private import table: a new
    entry's ``from <module> import <class>`` must appear in the emitted script.
    """
    entry = CapabilityEntry(module="fake_harness.mod", class_name="MonkeyCap")
    monkeypatch.setitem(CAPABILITY_REGISTRY, "MonkeyCap", entry)

    src = render_script(_make_version(capabilities=[{"name": "MonkeyCap", "config": {}}]))
    assert "from fake_harness.mod import MonkeyCap" in src
    assert "MonkeyCap()" in src


def test_registry_addition_is_visible_to_export_module_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``export`` names the same registry object, so a live addition reaches it."""
    entry = CapabilityEntry(module="fake_harness.mod", class_name="MonkeyCap")
    monkeypatch.setitem(CAPABILITY_REGISTRY, "MonkeyCap", entry)
    assert export.CAPABILITY_REGISTRY is CAPABILITY_REGISTRY
    assert "MonkeyCap" in export.CAPABILITY_REGISTRY


def test_registry_addition_is_visible_to_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """``build_capabilities`` locates a capability's class through the registry.

    A registered-but-unimportable entry gets past the "unknown capability" guard and fails
    only when the import is attempted — proving the name came from the shared registry.
    """
    entry = CapabilityEntry(module="fake_harness.mod", class_name="MonkeyCap")
    monkeypatch.setitem(CAPABILITY_REGISTRY, "MonkeyCap", entry)

    spec = CapabilitySpec.model_construct(name="MonkeyCap", config={})
    with pytest.raises(ConfigError) as exc:
        build_capabilities([spec])
    message = str(exc.value)
    assert "MonkeyCap" in message
    # It failed at import, not at the unknown-name guard.
    assert "valid names" not in message


# --- SCALAR_TYPES -------------------------------------------------------------


@pytest.mark.parametrize(
    "field_type,name",
    [
        (FieldType.STR, "str"),
        (FieldType.INT, "int"),
        (FieldType.FLOAT, "float"),
        (FieldType.BOOL, "bool"),
    ],
)
def test_scalar_type_name_matches_expected(field_type: FieldType, name: str) -> None:
    """``SCALAR_TYPES[ft].__name__`` yields the source type name export derives."""
    assert SCALAR_TYPES[field_type].__name__ == name


def test_scalar_types_covers_only_the_four_scalar_field_types() -> None:
    """SCALAR_TYPES maps the four scalar field types and excludes the enum type."""
    assert set(SCALAR_TYPES) == {FieldType.STR, FieldType.INT, FieldType.FLOAT, FieldType.BOOL}
    assert FieldType.ENUM not in SCALAR_TYPES
