"""Render an EvaluatorVersion to a standalone, runnable Python script."""

import inspect
import sys

from evalcore.models import EvaluatorVersion, FieldType, OutputField, parse_output_fields
from evalcore.tools import TOOL_REGISTRY

_BASE_TYPES: dict[FieldType, str] = {
    FieldType.STR: "str",
    FieldType.INT: "int",
    FieldType.FLOAT: "float",
    FieldType.BOOL: "bool",
}

_CAPABILITY_IMPORTS: dict[str, tuple[str, str]] = {
    "CodeMode": ("pydantic_ai_harness", "CodeMode"),
    "FileSystem": ("pydantic_ai_harness", "FileSystem"),
    "Shell": ("pydantic_ai_harness", "Shell"),
    "SubAgents": ("pydantic_ai_harness.subagents", "SubAgents"),
    "Planning": ("pydantic_ai_harness.planning", "Planning"),
}

_INLINE_CONST_TYPES = (int, float, complex, bool, str, bytes, tuple, frozenset, type(None))


class _Imports:
    """Accumulates the imports a rendered script needs, grouped stdlib vs third-party."""

    def __init__(self) -> None:
        self.plain: set[str] = set()
        self.frm: dict[str, set[str]] = {}

    def add_plain(self, module: str) -> None:
        """Record an ``import <module>`` statement."""
        self.plain.add(module)

    def add_from(self, module: str, name: str) -> None:
        """Record a ``from <module> import <name>`` statement."""
        self.frm.setdefault(module, set()).add(name)

    def render(self) -> str:
        """Render the accumulated imports as isort-style stdlib then third-party blocks."""
        stdlib: list[str] = []
        third: list[str] = []
        for module in sorted(self.plain):
            (stdlib if _is_stdlib(module) else third).append(f"import {module}")
        for module in sorted(self.frm):
            names = ", ".join(sorted(self.frm[module]))
            line = f"from {module} import {names}"
            (stdlib if _is_stdlib(module) else third).append(line)
        blocks = [b for b in ("\n".join(stdlib), "\n".join(third)) if b]
        return "\n\n".join(blocks)


def _is_stdlib(module: str) -> bool:
    """Return True if the top-level package of ``module`` is part of the standard library."""
    return module.split(".")[0] in sys.stdlib_module_names


def _py_str(text: str) -> str:
    """Return a Python source literal for text, preferring a readable triple-quoted form."""
    if "\\" not in text and '"""' not in text and "\r" not in text and not text.endswith('"'):
        return f'"""{text}"""'
    return repr(text)


def _annotation(field: OutputField) -> str:
    """Return the type annotation source for one output field."""
    if field.type is FieldType.ENUM:
        base = "Literal[" + ", ".join(repr(v) for v in field.enum_values or []) + "]"
    else:
        base = _BASE_TYPES[field.type]
    return f"{base} | None" if not field.required else base


def _field_call(field: OutputField) -> str:
    """Return the ``Field(...)`` source for one output field."""
    args = [f"description={field.description!r}"]
    if field.minimum is not None:
        args.append(f"ge={field.minimum!r}")
    if field.maximum is not None:
        args.append(f"le={field.maximum!r}")
    if not field.required:
        args.append("default=None")
    return "Field(" + ", ".join(args) + ")"


def _render_output_model(fields: list[OutputField]) -> str:
    """Render the output schema as a literal ``class OutputModel(BaseModel)`` definition."""
    lines = [
        "class OutputModel(BaseModel):",
        '    """Structured output schema for this evaluator."""',
        "",
    ]
    if not fields:
        lines.append("    pass")
    for field in fields:
        lines.append(f"    {field.name}: {_annotation(field)} = {_field_call(field)}")
    return "\n".join(lines)


def _tool_dependencies(fn: object, imports: _Imports) -> dict[str, object]:
    """Record the imports ``fn`` needs and return the module-level constants it references."""
    consts: dict[str, object] = {}
    glb = getattr(fn, "__globals__", {})
    for name in fn.__code__.co_names:  # type: ignore[attr-defined]
        if name not in glb:
            continue
        obj = glb[name]
        if inspect.ismodule(obj):
            module = obj.__name__
            if module == name:
                imports.add_plain(module)
            else:
                imports.add_from(module, name)  # unusual; best-effort
        elif isinstance(obj, _INLINE_CONST_TYPES):
            consts[name] = obj
        else:
            module = getattr(obj, "__module__", None)
            if module and module != "builtins" and not module.startswith("evalcore"):
                imports.add_from(module, name)
    return consts


def _render_tools(names: list[str], imports: _Imports) -> str:
    """Render each selected tool's source verbatim, preceded by any constants it references."""
    consts: dict[str, object] = {}
    sources: list[str] = []
    for name in names:
        spec = TOOL_REGISTRY.get(name)
        if spec is None:
            continue
        consts.update(_tool_dependencies(spec.fn, imports))
        sources.append(inspect.getsource(spec.fn).rstrip("\n"))
    parts: list[str] = []
    if consts:
        parts.append("\n".join(f"{cname} = {cval!r}" for cname, cval in consts.items()))
    parts.extend(sources)
    return "\n\n\n".join(parts)


def _render_capability(cap: dict) -> str:
    """Render one capability spec as a constructor call using its stored config."""
    config = cap.get("config") or {}
    args = ", ".join(f"{key}={value!r}" for key, value in config.items())
    return f"{cap['name']}({args})"


def _docstring(version: EvaluatorVersion) -> str:
    """Compose the module docstring naming the evaluator version and its gateway requirement."""
    run = (
        "uv run --with 'pydantic-ai>=2.19' "
        "--with 'pydantic-ai-harness[code-mode]>=0.12' script.py < row.json"
    )
    text = (
        f"Standalone evaluator: {version.version_name} (version {version.id}).\n\n"
        "Generated by eval-core from an EvaluatorVersion. This script is self-contained "
        "and has no eval-core dependency.\n\n"
        "Set PYDANTIC_AI_GATEWAY_API_KEY in the environment before running.\n\n"
        f"Run:\n    {run}"
    )
    return _py_str(text)


def render_script(version: EvaluatorVersion) -> str:
    """Render an EvaluatorVersion to a standalone, runnable Python script."""
    fields = parse_output_fields(version)

    imports = _Imports()
    imports.add_plain("asyncio")
    imports.add_plain("json")
    imports.add_plain("sys")
    imports.add_from("functools", "cache")
    imports.add_from("pydantic", "BaseModel")
    imports.add_from("pydantic", "Field")
    imports.add_from("pydantic_ai", "Agent")
    if any(f.type is FieldType.ENUM for f in fields):
        imports.add_from("typing", "Literal")
    for cap in version.capabilities:
        entry = _CAPABILITY_IMPORTS.get(cap["name"])
        if entry is not None:
            imports.add_from(*entry)

    tools_block = _render_tools(version.tools, imports)
    output_model = _render_output_model(fields)

    tool_refs = ", ".join(n for n in version.tools if n in TOOL_REGISTRY)
    cap_refs = ", ".join(_render_capability(cap) for cap in version.capabilities)

    agent_lines = [
        "@cache",
        "def _agent() -> Agent:",
        '    """Build the evaluator agent (cached across calls)."""',
        "    return Agent(",
        "        MODEL,",
        "        output_type=OutputModel,",
        "        instructions=INSTRUCTIONS,",
    ]
    if tool_refs:
        agent_lines.append(f"        tools=[{tool_refs}],")
    if cap_refs:
        agent_lines.append(f"        capabilities=[{cap_refs}],")
    agent_lines.append("    )")
    agent_block = "\n".join(agent_lines)

    sections = [
        _docstring(version),
        imports.render(),
        output_model,
    ]
    if tools_block:
        sections.append(tools_block)
    sections.append(f"MODEL = {version.model!r}")
    sections.append(f"INSTRUCTIONS = {_py_str(version.instructions)}")
    sections.append(agent_block)
    sections.append(f"PROMPT_TEMPLATE = {_py_str(version.prompt_template)}")
    sections.append(
        "async def evaluate(row: dict) -> OutputModel:\n"
        '    """Format the prompt with one row and run the evaluator, returning its output."""\n'
        "    result = await _agent().run(PROMPT_TEMPLATE.format(**row))\n"
        "    return result.output"
    )
    sections.append(
        'if __name__ == "__main__":\n'
        "    row = json.load(sys.stdin)\n"
        "    output = asyncio.run(evaluate(row))\n"
        "    print(output.model_dump_json(indent=2))"
    )
    return "\n\n\n".join(sections) + "\n"
