"""Render an EvaluatorVersion to a standalone, runnable Python script.

Beyond the single-file script (``render_script``), this module renders the two Python halves of
a portable eval package: a dataset module that rebuilds a ``pydantic_evals.Dataset`` and the
``ValcoreJudge`` companion module a package's dataset references. All three share the private
output-model and tool renderers so the artifacts never drift from one another.
"""

import inspect
import sys
from collections.abc import Sequence

from valcore.capabilities import CAPABILITY_REGISTRY
from valcore.models import (
    SCALAR_TYPES,
    Dataset,
    DatasetRow,
    EvaluatorVersion,
    FieldType,
    OutputField,
    parse_output_fields,
)
from valcore.tools import TOOL_REGISTRY

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
        base = SCALAR_TYPES[field.type].__name__
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
            if module and module != "builtins" and not module.startswith("valcore"):
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
        "Generated by valcore from an EvaluatorVersion. This script is self-contained "
        "and has no valcore dependency.\n\n"
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
        entry = CAPABILITY_REGISTRY.get(cap["name"])
        if entry is not None:
            imports.add_from(entry.script_module, entry.class_name)

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


def render_tool_sources(names: Sequence[str]) -> tuple[str, str]:
    """Return the (imports, source) blocks the named tools need, for embedding in a
    generated module.

    Thin public wrapper over the same ``_Imports`` / ``_render_tools`` helpers ``render_script``
    uses, so an embedding module inlines a tool identically to how the script does. An empty
    name list yields two empty strings.
    """
    imports = _Imports()
    source = _render_tools([n for n in names], imports)
    return imports.render(), source


def _dataset_docstring(dataset: Dataset) -> str:
    """Compose the dataset module's docstring, naming it and disclaiming any valcore dependency."""
    text = (
        f"Standalone dataset: {dataset.name}.\n\n"
        "Generated by valcore. This module builds a pydantic_evals.Dataset directly and has no "
        "valcore dependency."
    )
    return _py_str(text)


def render_dataset_module(dataset: Dataset, rows: list[DatasetRow]) -> str:
    """Render a dataset as a module that builds a ``pydantic_evals.Dataset``.

    Every value is emitted via ``repr()`` so strings, numbers, and nested dicts round-trip
    exactly. A row with no label omits ``expected_output``; row provenance is emitted as
    ``metadata=`` only when there is any to carry.
    """
    case_lines: list[str] = []
    for row in rows:
        args = [f"name={row.id!r}", f"inputs={row.data!r}"]
        if row.label is not None:
            args.append(f"expected_output={row.label['value']!r}")
        metadata = _row_metadata(row)
        if metadata:
            args.append(f"metadata={metadata!r}")
        case_lines.append("        Case(" + ", ".join(args) + "),")

    cases_block = "\n".join(case_lines)
    body = f"DATASET = Dataset(\n    name={dataset.name!r},\n    cases=[\n{cases_block}\n    ],\n)"
    sections = [
        _dataset_docstring(dataset),
        "from pydantic_evals import Case, Dataset",
        body,
    ]
    return "\n\n\n".join(sections) + "\n"


def _row_metadata(row: DatasetRow) -> dict:
    """Return the provenance a case should carry, or an empty dict when the row has none."""
    metadata: dict = {}
    if row.note is not None:
        metadata["note"] = row.note
    if row.label_reasoning is not None:
        metadata["label_reasoning"] = row.label_reasoning
    if row.label_source is not None:
        metadata["label_source"] = row.label_source.value
    if row.suggested_label is not None:
        metadata["suggested_label"] = row.suggested_label
    return metadata


def render_judge_module(version: EvaluatorVersion, package_filename: str) -> str:
    """Render the ``ValcoreJudge`` companion module for a package.

    The emitted evaluator reads its package with stdlib ``json`` and builds the agent via
    ``Agent.from_spec``, passing an explicit ``output_type`` so the result is a real model with
    attribute access rather than a ``StructuredDict``. The module imports neither valcore nor
    yaml; its ``OutputModel`` comes from the same ``_render_output_model`` the script uses, so
    the two artifacts describe the output identically.
    """
    fields = parse_output_fields(version)

    imports = _Imports()
    imports.add_plain("json")
    imports.add_from("pathlib", "Path")
    imports.add_from("dataclasses", "dataclass")
    imports.add_from("pydantic", "BaseModel")
    imports.add_from("pydantic", "Field")
    imports.add_from("pydantic_ai", "Agent")
    imports.add_from("pydantic_ai.agent.spec", "AgentSpec")
    imports.add_from("pydantic_evals.evaluators", "Evaluator")
    if any(f.type is FieldType.ENUM for f in fields):
        imports.add_from("typing", "Literal")

    cap_types: list[str] = []
    for cap in version.capabilities:
        entry = CAPABILITY_REGISTRY.get(cap["name"])
        if entry is not None:
            imports.add_from(entry.script_module, entry.class_name)
            cap_types.append(entry.class_name)

    tool_imports, tools_block = render_tool_sources(version.tools)
    output_model = _render_output_model(fields)
    tool_refs = ", ".join(n for n in version.tools if n in TOOL_REGISTRY)

    from_spec_lines = [
        "        agent = Agent.from_spec(",
        "            AgentSpec.from_dict(agent_doc),",
        "            # An explicit output_type beats the spec's output_schema, so the judge",
        "            # gets a real model with attribute access rather than a plain dict.",
        "            output_type=OutputModel,",
    ]
    if tool_refs:
        from_spec_lines.append(f"            tools=[{tool_refs}],")
    if cap_types:
        from_spec_lines.append(f"            custom_capability_types=[{', '.join(cap_types)}],")
    from_spec_lines.append("            defer_model_check=True,")
    from_spec_lines.append("        )")
    from_spec_block = "\n".join(from_spec_lines)

    judge_class = "\n".join(
        [
            "@dataclass",
            "class ValcoreJudge(Evaluator):",
            '    """Score one case by running the package\'s evaluator agent."""',
            "",
            f"    package: str = {package_filename!r}",
            "",
            "    async def evaluate(self, ctx) -> str | float:",
            "        doc = json.loads(Path(self.package).read_text())",
            "        # A split .agent.json has the agent keys at the document root; a bundle",
            '        # nests them under "agent". Falling back to the root normalizes both.',
            '        agent_doc = doc.get("agent", doc)',
            '        vc = doc["valcore"]',
            from_spec_block,
            '        result = await agent.run(vc["prompt_template"].format(**ctx.inputs))',
            '        return getattr(result.output, vc["score_field"])',
        ]
    )

    imports_block = imports.render()
    if tool_imports:
        imports_block = imports_block + "\n" + tool_imports

    sections = [
        _judge_docstring(version),
        imports_block,
        output_model,
    ]
    if tools_block:
        sections.append(tools_block)
    sections.append(judge_class)
    return "\n\n\n".join(sections) + "\n"


def _judge_docstring(version: EvaluatorVersion) -> str:
    """Compose the judge module's docstring, naming the evaluator and disclaiming valcore."""
    text = (
        f"ValcoreJudge companion module for evaluator {version.version_name}.\n\n"
        "Generated by valcore. Reads its eval package with stdlib json and rebuilds the agent "
        "via Agent.from_spec; it has no valcore dependency."
    )
    return _py_str(text)
