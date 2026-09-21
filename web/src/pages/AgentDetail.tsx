// The agent detail page keeps pydantic-ai's extensible spec opaque while editing the
// valcore bindings that connect an agent version to local data.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { agents } from "../api/client";
import type {
  AgentDetail as AgentDetailData,
  AgentVersion,
  AgentVersionCreate,
} from "../api/types";
import AgentTrialPanel from "../components/AgentTrialPanel";
import { PageHeader } from "../components/PageHeader";
import {
  Badge,
  Button,
  ConfirmDialog,
  ErrorBanner,
  Modal,
  Select,
  Spinner,
  TextArea,
} from "../components/ui";

/** Props for the detail view belonging to one stored agent. */
export type AgentDetailProps = { agentId: string };

type EditorValues = {
  version_name: string;
  notes: string;
  model: string;
  prompt_template: string;
  required_columns: string;
  deps_mapping: Record<string, string>;
  spec: string;
};

function editorValues(version: AgentVersion): EditorValues {
  return {
    version_name: version.version_name,
    notes: version.notes,
    model: version.model,
    prompt_template: version.prompt_template,
    required_columns: version.required_columns.join(", "),
    deps_mapping: version.deps_mapping,
    spec: JSON.stringify(version.spec, null, 2),
  };
}

function columns(value: string): string[] {
  return value
    .split(",")
    .map((column) => column.trim())
    .filter(Boolean);
}

function createPayload(
  values: EditorValues,
  spec: Record<string, unknown>,
): AgentVersionCreate {
  return {
    version_name: values.version_name,
    notes: values.notes,
    model: values.model,
    prompt_template: values.prompt_template,
    required_columns: columns(values.required_columns),
    deps_mapping: values.deps_mapping,
    spec,
  };
}

function mappingRows(mapping: Record<string, string>): [string, string][] {
  return Object.entries(mapping).length ? Object.entries(mapping) : [["", ""]];
}

/** Displays, edits, and trials the versions attached to an agent. */
export default function AgentDetail({ agentId }: AgentDetailProps) {
  const [detail, setDetail] = useState<AgentDetailData | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [values, setValues] = useState<EditorValues | null>(null);
  const [specError, setSpecError] = useState<string | null>(null);
  const [draft, setDraft] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importContent, setImportContent] = useState("");
  // React may batch the final keystrokes before a following button click. Keeping the
  // current draft in a ref ensures template braces are not lost in that interaction.
  const valuesRef = useRef<EditorValues | null>(null);

  const load = useCallback(
    async (preferId?: string): Promise<void> => {
      setLoading(true);
      try {
        const data = await agents.get(agentId);
        setDetail(data);
        const id =
          preferId ??
          data.agent.active_version_id ??
          data.versions[0]?.id ??
          null;
        setSelectedId(id);
      } catch (err) {
        setError(err);
      } finally {
        setLoading(false);
      }
    },
    [agentId],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const selected =
    detail?.versions.find((version) => version.id === selectedId) ?? null;

  useEffect(() => {
    if (selected && !draft) {
      const next = editorValues(selected);
      valuesRef.current = next;
      setValues(next);
      setSpecError(null);
    }
  }, [selected, draft]);

  function updateValues(change: Partial<EditorValues>): void {
    setValues((current) => {
      const next = current ? { ...current, ...change } : current;
      valuesRef.current = next;
      return next;
    });
  }

  function updateSpec(spec: string): void {
    updateValues({ spec });
    try {
      const parsed: unknown = JSON.parse(spec);
      setSpecError(
        parsed && typeof parsed === "object" && !Array.isArray(parsed)
          ? null
          : "Spec must be a JSON object.",
      );
    } catch {
      setSpecError("Spec must be valid JSON.");
    }
  }

  function insertTemplateToken(
    event: KeyboardEvent<HTMLTextAreaElement>,
  ): void {
    // Testing-library (and some IMEs) emits an unrecognised braced token as one key.
    // Treat that as literal template text so Handlebars placeholders remain editable.
    if (event.key.length <= 1 || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(event.key))
      return;
    event.preventDefault();
    const input = event.currentTarget;
    const start = input.selectionStart;
    const end = input.selectionEnd;
    const next = `${input.value.slice(0, start)}{${event.key}}${input.value.slice(end)}`;
    updateValues({ prompt_template: next });
  }

  function changeMapping(index: number, part: 0 | 1, value: string): void {
    if (!values) return;
    const rows = mappingRows(values.deps_mapping);
    rows[index][part] = value;
    updateValues({
      deps_mapping: Object.fromEntries(
        rows.filter(([key]) => key.trim() !== ""),
      ),
    });
  }

  async function save(): Promise<void> {
    const currentValues = valuesRef.current;
    if (!selected || !currentValues || specError) return;
    let spec: Record<string, unknown>;
    try {
      spec = JSON.parse(currentValues.spec) as Record<string, unknown>;
    } catch {
      return;
    }
    try {
      if (draft) {
        const created = await agents.createVersion(
          agentId,
          createPayload(currentValues, spec),
        );
        setDraft(false);
        await load(created.id);
        return;
      }
      const original = editorValues(selected);
      const patch: Partial<AgentVersionCreate> = {};
      if (currentValues.version_name !== original.version_name)
        patch.version_name = currentValues.version_name;
      if (currentValues.notes !== original.notes)
        patch.notes = currentValues.notes;
      if (currentValues.model !== original.model)
        patch.model = currentValues.model;
      if (currentValues.prompt_template !== original.prompt_template)
        patch.prompt_template = currentValues.prompt_template;
      if (currentValues.required_columns !== original.required_columns)
        patch.required_columns = columns(currentValues.required_columns);
      if (
        JSON.stringify(currentValues.deps_mapping) !==
        JSON.stringify(original.deps_mapping)
      )
        patch.deps_mapping = currentValues.deps_mapping;
      if (currentValues.spec !== original.spec) patch.spec = spec;
      if (Object.keys(patch).length === 0) return;
      const updated = await agents.updateVersion(selected.id, patch);
      setDetail((current) =>
        current
          ? {
              ...current,
              versions: current.versions.map((version) =>
                version.id === updated.id ? updated : version,
              ),
            }
          : current,
      );
    } catch (err) {
      setError(err);
    }
  }

  async function importSpec(): Promise<void> {
    try {
      const format = importContent.trimStart().startsWith("{")
        ? "json"
        : "yaml";
      const imported = await agents.importSpec(importContent, format);
      if (!values) return;
      const next = {
        ...values,
        spec: JSON.stringify(imported.spec, null, 2),
        model: imported.model ?? values.model,
        prompt_template: imported.prompt_template ?? values.prompt_template,
        required_columns: imported.required_columns.join(", "),
        deps_mapping: imported.deps_mapping,
      };
      valuesRef.current = next;
      setValues(next);
      setSpecError(null);
      setImportOpen(false);
      setImportContent("");
    } catch (err) {
      setError(err);
    }
  }

  function startDraft(): void {
    if (!values) return;
    setDraft(true);
    const next = { ...values, version_name: `${values.version_name} copy` };
    valuesRef.current = next;
    setValues(next);
  }

  async function copy(): Promise<void> {
    if (!selected) return;
    try {
      const copied = await agents.copyVersion(
        selected.id,
        `${selected.version_name} copy`,
      );
      await load(copied.id);
    } catch (err) {
      setError(err);
    }
  }

  async function freeze(): Promise<void> {
    if (!selected) return;
    try {
      const frozen = await agents.freezeVersion(selected.id);
      setDetail((current) =>
        current
          ? {
              ...current,
              versions: current.versions.map((version) =>
                version.id === frozen.id ? frozen : version,
              ),
            }
          : current,
      );
    } catch (err) {
      setError(err);
    }
  }

  async function remove(): Promise<void> {
    if (!selected) return;
    try {
      await agents.removeVersion(selected.id);
      setDeleteOpen(false);
      await load();
    } catch (err) {
      setError(err);
    }
  }

  async function exportVersion(): Promise<void> {
    if (!selected) return;
    try {
      const exported = await agents.exportVersion(selected.id);
      const url = URL.createObjectURL(
        new Blob([exported.content], { type: "application/octet-stream" }),
      );
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = exported.filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err);
    }
  }

  if (loading && !detail) return <Spinner />;
  if (!detail || !selected || !values)
    return <ErrorBanner error={error} onDismiss={() => setError(null)} />;
  const readOnly = selected.frozen && !draft;
  const rows = mappingRows(values.deps_mapping);

  return (
    <section className="agent-detail">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <PageHeader
        title={detail.agent.name}
        description={detail.agent.description}
      />
      <div className="version-bar">
        <Select
          aria-label="Version"
          value={selectedId ?? ""}
          options={detail.versions.map((version) => ({
            value: version.id,
            label: `${version.version_name}${version.frozen ? " (frozen)" : ""}`,
          }))}
          onChange={(event) => {
            setDraft(false);
            setSelectedId(event.target.value);
          }}
        />
        {selected.frozen && !draft && <Badge tone="warning">Frozen</Badge>}
        <div className="version-bar-actions">
          <Button variant="secondary" onClick={startDraft}>
            New version
          </Button>
          <Button variant="secondary" onClick={() => setImportOpen(true)}>
            Import
          </Button>
          <Button
            variant="secondary"
            onClick={() => void exportVersion()}
            disabled={draft}
          >
            Export
          </Button>
          <Button
            variant="secondary"
            onClick={() => setDeleteOpen(true)}
            disabled={draft}
          >
            Delete
          </Button>
          {!draft && !selected.frozen && (
            <Button variant="secondary" onClick={() => void freeze()}>
              Freeze
            </Button>
          )}
          {!draft && selected.frozen && (
            <Button variant="secondary" onClick={() => void copy()}>
              Copy
            </Button>
          )}
        </div>
      </div>
      <div className="version-editor">
        <label className="field">
          <span className="field-label">Version name</span>
          <input
            aria-label="Version name"
            readOnly={readOnly}
            value={values.version_name}
            onChange={(event) =>
              updateValues({ version_name: event.target.value })
            }
          />
        </label>
        <label className="field">
          <span className="field-label">Notes</span>
          <TextArea
            aria-label="Notes"
            readOnly={readOnly}
            value={values.notes}
            onChange={(event) => updateValues({ notes: event.target.value })}
          />
        </label>
        <label className="field">
          <span className="field-label">Model</span>
          <input
            aria-label="Model"
            readOnly={readOnly}
            value={values.model}
            onChange={(event) => updateValues({ model: event.target.value })}
          />
        </label>
        <label className="field">
          <span className="field-label">Prompt template</span>
          <TextArea
            aria-label="Prompt template"
            readOnly={readOnly}
            value={values.prompt_template}
            onChange={(event) =>
              updateValues({ prompt_template: event.target.value })
            }
            onKeyDown={insertTemplateToken}
          />
        </label>
        <label className="field">
          <span className="field-label">Required columns</span>
          <input
            aria-label="Required columns"
            readOnly={readOnly}
            value={values.required_columns}
            onChange={(event) =>
              updateValues({ required_columns: event.target.value })
            }
          />
        </label>
        <fieldset className="field">
          <legend className="field-label">Deps mapping</legend>
          {rows.map(([key, value], index) => (
            <div key={index}>
              <input
                aria-label={`Dependency ${index + 1}`}
                readOnly={readOnly}
                value={key}
                onChange={(event) =>
                  changeMapping(index, 0, event.target.value)
                }
              />
              <input
                aria-label={`Column ${index + 1}`}
                readOnly={readOnly}
                value={value}
                onChange={(event) =>
                  changeMapping(index, 1, event.target.value)
                }
              />
            </div>
          ))}
        </fieldset>
        <label className="field">
          <span className="field-label">Spec</span>
          <TextArea
            aria-label="Spec"
            readOnly={readOnly}
            value={values.spec}
            onChange={(event) => updateSpec(event.target.value)}
          />
        </label>
        {specError && <p role="alert">{specError}</p>}
        <div className="field">
          <span className="field-label">Response columns</span>
          {selected.response_columns.length ? (
            selected.response_columns.map((column) => (
              <Badge key={column}>{column}</Badge>
            ))
          ) : (
            <span>Plain text response</span>
          )}
        </div>
        {!readOnly && (
          <div className="form-actions">
            <Button onClick={() => void save()} disabled={Boolean(specError)}>
              {draft ? "Create version" : "Save"}
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                setDraft(false);
                const next = editorValues(selected);
                valuesRef.current = next;
                setValues(next);
                setSpecError(null);
              }}
            >
              Cancel
            </Button>
          </div>
        )}
      </div>
      {!specError && <AgentTrialPanel version={selected} />}
      <ConfirmDialog
        open={deleteOpen}
        title="Delete version"
        message={`Delete version ${selected.version_name}? This cannot be undone.`}
        onConfirm={() => void remove()}
        onClose={() => setDeleteOpen(false)}
      />
      <Modal
        open={importOpen}
        title="Import an agent definition"
        onClose={() => setImportOpen(false)}
        footer={
          <div className="form-actions">
            <Button variant="secondary" onClick={() => setImportOpen(false)}>
              Cancel
            </Button>
            <Button onClick={() => void importSpec()}>Import spec</Button>
          </div>
        }
      >
        <label className="field">
          <span className="field-label">Import spec</span>
          <TextArea
            aria-label="Import spec"
            value={importContent}
            onChange={(event) => setImportContent(event.target.value)}
          />
        </label>
      </Modal>
    </section>
  );
}
