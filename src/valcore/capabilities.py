"""The single home for valcore's harness capability registry.

Three modules once described the same five harness capabilities independently —
``models.VALID_CAPABILITIES`` (a name set), ``factory._CAPABILITY_MODULES`` (name to
import path), and ``export._CAPABILITY_IMPORTS`` (name to module/class for rendering).
Keeping them in step was manual and they drifted (``factory`` and ``export`` disagreed on
``CodeMode``'s module). This module collapses all three into one table so a capability is
added, removed, or repointed in exactly one place.

It imports only the domain configuration error from valcore, so ``models`` can depend on it
without a cycle.
"""

from dataclasses import dataclass
from importlib import import_module

from valcore.errors import ConfigError


@dataclass(frozen=True)
class CapabilityEntry:
    """Where a harness capability class lives, for both live import and code rendering.

    ``module`` is the canonical runtime module — the deep path ``factory.build_capabilities``
    imports at run time, and what a bad entry is checked against in the tests.

    ``render_module`` is the module an *exported* standalone script imports the class from.
    It defaults to ``module`` and is only set where valcore historically emitted a shorter
    re-export path (``pydantic_ai_harness`` re-exports several capabilities from its root).
    Keeping it distinct is what lets the registry stay the single source for both a deep
    canonical runtime path and a byte-identical ``render_script`` output — a single module
    could not be both at once.
    """

    module: str
    class_name: str
    render_module: str | None = None

    @property
    def script_module(self) -> str:
        """The module an exported standalone script imports this capability from."""
        return self.render_module or self.module


# The deep module path is canonical for each capability: it is what the live runtime
# imports in ``factory.build_capabilities``. ``pydantic_ai_harness`` re-exports CodeMode,
# FileSystem, and Shell from its package root, and exported scripts import them from there
# (``render_module``) to keep ``render_script`` output byte-identical with prior releases.
CAPABILITY_REGISTRY: dict[str, CapabilityEntry] = {
    "CodeMode": CapabilityEntry(
        "pydantic_ai_harness.code_mode", "CodeMode", render_module="pydantic_ai_harness"
    ),
    "SubAgents": CapabilityEntry("pydantic_ai_harness.subagents", "SubAgents"),
    "Planning": CapabilityEntry("pydantic_ai_harness.planning", "Planning"),
    "FileSystem": CapabilityEntry(
        "pydantic_ai_harness.filesystem", "FileSystem", render_module="pydantic_ai_harness"
    ),
    "Shell": CapabilityEntry(
        "pydantic_ai_harness.shell", "Shell", render_module="pydantic_ai_harness"
    ),
}

VALID_CAPABILITIES: frozenset[str] = frozenset(CAPABILITY_REGISTRY)


def load_capability_class(entry: CapabilityEntry) -> type:
    """Import and return the class an entry names, as ConfigError on failure."""
    try:
        module = import_module(entry.module)
        return getattr(module, entry.class_name)
    except (ImportError, AttributeError) as exc:
        raise ConfigError(f"Capability {entry.class_name!r} could not be imported: {exc}") from exc


def spec_capability_types() -> list[type]:
    """Return harness capability classes that can be reconstructed from an agent spec.

    Capabilities without a serialization name are excluded because their JSON spec cannot
    identify a class that ``Agent.from_spec`` can reconstruct. Missing optional harness
    extras are skipped so partially installed environments retain their available features.
    """
    capability_types: list[type] = []
    for entry in CAPABILITY_REGISTRY.values():
        try:
            capability_type = load_capability_class(entry)
        except ConfigError:
            continue
        if capability_type.get_serialization_name() is not None:
            capability_types.append(capability_type)
    return capability_types


def spec_capability_names() -> frozenset[str]:
    """Return serialization names for harness capabilities usable in stored agent specs."""
    return frozenset(
        name
        for capability_type in spec_capability_types()
        if (name := capability_type.get_serialization_name()) is not None
    )
