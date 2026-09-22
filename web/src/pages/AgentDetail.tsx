// The agent detail page keeps pydantic-ai's extensible spec opaque while editing the
// valcore bindings that connect an agent version to local data.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { useNavigate } from "react-router-dom";
import { api, agents, datasets, runs } from "../api/client";
import type {
  AgentDetail as AgentDetailData,
  AgentVersion,
  AgentVersionCreate,
  DatasetSummary,
} from "../api/types";
import AgentTrialPanel from "../components/AgentTrialPanel";
import type { AppConfig } from "../components/VersionEditor";
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
  deps_mapping: [string, string][];
  spec: string;
};

function editorValues(version: AgentVersion): EditorValues {
  return {
    version_name: version.version_name,
    notes: version.notes,
    model: version.model,
    prompt_template: version.prompt_template,
    required_columns: version.required_columns.join(", "),
    deps_mapping: mappingRows(version.deps_mapping),
    spec: JSON.stringify(version.spec, null, 2),
  };
}

// A newly created agent has no versions, so its editor opens on a blank draft rather
// than having nothing to select. The server rejects a version with no required columns,
// so the draft starts on a single `input` binding that saves without further editing.
function blankValues(): EditorValues {
  return {
    version_name: "v1",
    notes: "",
    model: "",
    prompt_template: "{input}",
    required_columns: "input",
    deps_mapping: [["", ""]],
    spec: "{}",
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
    deps_mapping: mappingObject(values.deps_mapping),
    spec,
  };
}

function mappingRows(mapping: Record<string, string>): [string, string][] {
  return Object.entries(mapping).length ? Object.entries(mapping) : [["", ""]];
}

function mappingObject(rows: [string, string][]): Record<string, string> {
  return Object.fromEntries(rows.filter(([key]) => key.trim() !== ""));
}

/** Displays, edits, and trials the versions attached to an agent. */
export default function AgentDetail({ agentId }: AgentDetailProps) {
  const navigate = useNavigate();
  const [detail, setDetail] = useState<AgentDetailData | null>(null);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [values, setValues] = useState<EditorValues | null>(null);
  const [specError, setSpecError] = useState<string | null>(null);
  const [draft, setDraft] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importContent, setImportContent] = useState("");
  const [runOpen, setRunOpen] = useState(false);
  const [datasetsForRun, setDatasetsForRun] = useState<DatasetSummary[]>([]);
  const [datasetId, setDatasetId] = useState("");
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

  useEffect(() => {
    api<AppConfig>("/api/config").then(setConfig).catch(setError);
  }, []);

  const selected =
    detail?.versions.find((version) => version.id === selectedId) ?? null;
  // An agent with no versions has nothing to select, so it opens straight into a draft.
  const empty = detail !== null && detail.versions.length === 0;
  const showDraft = draft || empty;

  useEffect(() => {
    if (selected && !draft) {
      const next = editorValues(selected);
      valuesRef.current = next;
      setValues(next);
      setSpecError(null);
    }
  }, [selected, draft]);

  // Seed the blank draft, then fill its model once the config's default arrives. An
  // already-typed model is left alone, so a late config never overwrites an edit.
  useEffect(() => {
    if (!empty) return;
    const current = valuesRef.current;
    if (current && current.model !== "") return;
    const next = {
      ...(current ?? blankValues()),
      model: config?.default_model ?? "",
    };
    valuesRef.current = next;
    setValues(next);
  }, [empty, config]);

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
    const rows = values.deps_mapping.map((row) => [...row] as [string, string]);
    rows[index][part] = value;
    updateValues({ deps_mapping: rows });
  }

  function addMapping(): void {
    if (!values) return;
    updateValues({ deps_mapping: [...values.deps_mapping, ["", ""]] });
  }

  async function save(): Promise<void> {
    const currentValues = valuesRef.current;
    if (!currentValues || specError) return;
    if (!showDraft && !selected) return;
    let spec: Record<string, unknown>;
    try {
      spec = JSON.parse(currentValues.spec) as Record<string, unknown>;
    } catch {
      return;
    }
    try {
      if (showDraft) {
        const created = await agents.createVersion(
          agentId,
          createPayload(currentValues, spec),
        );
        setDraft(false);
        await load(created.id);
        return;
      }
      if (!selected) return;
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
        JSON.stringify(mappingObject(currentValues.deps_mapping)) !==
        JSON.stringify(mappingObject(original.deps_mapping))
      )
        patch.deps_mapping = mappingObject(currentValues.deps_mapping);
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
        deps_mapping: mappingRows(imported.deps_mapping),
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

  async function openRun(): Promise<void> {
    try {
      const availableDatasets = await datasets.list();
      setDatasetsForRun(availableDatasets);
      setDatasetId(availableDatasets[0]?.id ?? "");
      setRunOpen(true);
    } catch (err) {
      setError(err);
    }
  }

  async function runOverDataset(): Promise<void> {
    if (!selected || !datasetId) return;
    try {
      const run = await runs.create({
        kind: "derive",
        version_id: selected.id,
        dataset_id: datasetId,
      });
      setRunOpen(false);
      navigate(`/runs/${run.id}`);
    } catch (err) {
      setError(err);
    }
  }

  if (loading && !detail) return <Spinner />;
  if (!detail)
    return <ErrorBanner error={error} onDismiss={() => setError(null)} />;
  if (!values) return <Spinner />;
  const readOnly = selected !== null && selected.frozen && !showDraft;
  const rows = values.deps_mapping;

  return (
    <section className="agent-detail">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <PageHeader
        title={detail.agent.name}
        description={detail.agent.description}
      />
      <div className="version-bar">
        {!empty && (
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
        )}
        {selected?.frozen && !draft && <Badge tone="warning">Frozen</Badge>}
        <div className="version-bar-actions">
          {!empty && (
            <Button variant="secondary" onClick={startDraft}>
              New version
            </Button>
          )}
          <Button variant="secondary" onClick={() => setImportOpen(true)}>
            Import
          </Button>
          {!empty && (
            <>
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
              {!draft && !selected?.frozen && (
                <Button variant="secondary" onClick={() => void freeze()}>
                  Freeze
                </Button>
              )}
              {!draft && selected?.frozen && (
                <Button variant="secondary" onClick={() => void copy()}>
                  Copy
                </Button>
              )}
            </>
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
          {!readOnly && (
            <Button variant="secondary" onClick={addMapping}>
              Add dependency mapping
            </Button>
          )}
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
          {selected?.response_columns.length ? (
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
              {showDraft ? "Create version" : "Save"}
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                setDraft(false);
                const next = selected ? editorValues(selected) : blankValues();
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
      {!specError && selected && (
        <>
          <AgentTrialPanel version={selected} />
          <div className="form-actions">
            <Button
              variant="secondary"
              onClick={() => void openRun()}
              disabled={!selected}
            >
              Run over a dataset
            </Button>
          </div>
        </>
      )}
      {!selected && (
        <div className="form-actions">
          <Button variant="secondary" disabled>
            Run over a dataset
          </Button>
        </div>
      )}
      <ConfirmDialog
        open={deleteOpen}
        title="Delete version"
        message={`Delete version ${selected?.version_name ?? ""}? This cannot be undone.`}
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
      <Modal
        open={runOpen}
        title="Run agent over a dataset"
        onClose={() => setRunOpen(false)}
        footer={
          <div className="form-actions">
            <Button variant="secondary" onClick={() => setRunOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => void runOverDataset()}
              disabled={!datasetId}
            >
              Run agent
            </Button>
          </div>
        }
      >
        <label className="field">
          <span className="field-label">Dataset</span>
          <Select
            aria-label="Dataset"
            value={datasetId}
            options={datasetsForRun.map((dataset) => ({
              value: dataset.id,
              label: dataset.name,
            }))}
            onChange={(event) => setDatasetId(event.target.value)}
          />
        </label>
      </Modal>
    </section>
  );
}
