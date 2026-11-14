"""Bind dataset storage to invocation roles without coupling evaluators to column names."""

from valcore.errors import ContractError


def check_mapping(
    version,
    columns: list[str],
    mapping: dict,
    *,
    has_invocation: bool,
    metadata_columns: list[str] | None = None,
) -> None:
    """Validate bindings before queuing work; recorded values are checked when read."""
    if not isinstance(mapping, dict) or set(mapping) - {"input", "output", "metadata"}:
        raise ContractError("Invocation mapping accepts only input, output, and metadata.")
    metadata_columns = columns if metadata_columns is None else metadata_columns
    metadata = mapping.get("metadata", {})
    if not isinstance(metadata, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in metadata.items()
    ):
        raise ContractError("Metadata mapping must map metadata names to column names.")
    for role in ("input", "output"):
        if role in mapping and (not isinstance(mapping[role], str) or mapping[role] not in columns):
            raise ContractError(f"Mapped {role} column {mapping[role]!r} is not available.")
    for key in version.required_columns:
        column = metadata.get(key, key)
        if column not in metadata_columns:
            raise ContractError(f"Required metadata {key!r} needs column {column!r}.")
    for key, column in metadata.items():
        if column not in metadata_columns:
            raise ContractError(f"Metadata {key!r} needs column {column!r}.")
    for role in ("output", "input"):
        if role == "input" and version.input_mode == "output":
            continue
        if not has_invocation and role not in mapping:
            raise ContractError(
                f"Map an {role} column for this evaluator, or select a recorded agent invocation."
            )


def bind_invocation(
    version, data: dict, mapping: dict, recorded: dict | None, *, metadata_data: dict | None = None
):
    """Missing and explicit null are different: null is a valid input or return value."""
    metadata_data = data if metadata_data is None else metadata_data
    check_mapping(
        version,
        list(data),
        mapping,
        has_invocation=recorded is not None,
        metadata_columns=list(metadata_data),
    )
    invocation = dict(recorded or {})
    for role in ("input", "output"):
        if role in mapping:
            invocation[role] = data[mapping[role]]
    if "output" not in invocation:
        raise ContractError("No recorded output; map an output column.")
    if version.input_mode == "input_output" and "input" not in invocation:
        raise ContractError(
            "No recorded input; map an input column or use an output-only evaluator."
        )
    if version.input_mode == "output":
        invocation.pop("input", None)
    names = dict.fromkeys(version.required_columns)
    names.update(mapping.get("metadata", {}))
    metadata = {key: metadata_data[mapping.get("metadata", {}).get(key, key)] for key in names}
    return metadata, invocation


def invocation_contexts(store, run, version, rows):
    """Keep source metadata separate from response overlays, even on name collisions."""
    if version.input_contract != "invocation":
        return {}
    link = store.get_run_derivation(run.id)
    responses = (
        {r.dataset_row_id: r for r in store.list_agent_responses(link.derivation_id)}
        if link
        else {}
    )
    source = {r.id: r.data for r in store.list_rows(run.dataset_id)}
    result = {}
    for row in rows:
        response = responses.get(row.id)
        recorded = response.invocation if response else None
        if response is not None and recorded is None and version.input_mode == "output":
            recorded = {"output": response.data}
        # Explicit mappings can reference the response projection. Metadata defaults to
        # original source values so an agent cannot replace request metadata by returning it.
        result[row.id] = bind_invocation(
            version, row.data, run.invocation_mapping, recorded, metadata_data=source[row.id]
        )
    return result
