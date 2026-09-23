// The agent detail page keeps pydantic-ai's extensible spec opaque while editing the
// valcore bindings that connect an agent version to local data.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useNavigate } from "react-router-dom";
import { api, agents, datasets, runs } from "../api/client";
import type {
  AgentDetail as AgentDetailData,
  AgentVersion,
  AgentVersionCreate,
  DatasetSummary,
  CapabilitySpec,
} from "../api/types";
import AgentTrialPanel from "../components/AgentTrialPanel";
import EvaluatorFromAgent from "../components/EvaluatorFromAgent";
import { CapabilitiesEditor } from "../components/CapabilitiesEditor";
import { useSetup } from "../components/useSetup";
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

// A new agent needs instructions, but no dataset input contract. Without a template,
// the runtime forwards the complete input after those instructions.
function blankValues(): EditorValues {
  return {
    version_name: "v1",
    notes: "",
    model: "",
    prompt_template: "",
    required_columns: "",
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

function specObject(source: string): Record<string, unknown> | null {
  try {
    const value: unknown = JSON.parse(source);
    return value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function specCapabilities(spec: Record<string, unknown> | null): CapabilitySpec[] {
  if (!Array.isArray(spec?.capabilities)) return [];
  return spec.capabilities.flatMap((entry: unknown) => {
    if (typeof entry === "string") return [{ name: entry, config: {} }];
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) return [];
    const capability = entry as Record<string, unknown>;
    if (typeof capability.name === "string") {
      const config = capability.config;
      return [{
        name: capability.name,
        config: config && typeof config === "object" && !Array.isArray(config)
          ? config as Record<string, unknown>
          : {},
      }];
    }
    const [name, config] = Object.entries(capability)[0] ?? [];
    return name ? [{
      name,
      config: config && typeof config === "object" && !Array.isArray(config)
        ? config as Record<string, unknown>
        : {},
    }] : [];
  });
}

/** Displays, edits, and trials the versions attached to an agent. */
export default function AgentDetail({ agentId }: AgentDetailProps) {
  const navigate = useNavigate();
  const [detail, setDetail] = useState<AgentDetailData | null>(null);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const { status: setupStatus } = useSetup();
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
  const [evaluatorOpen, setEvaluatorOpen] = useState(false);
  const [datasetsForRun, setDatasetsForRun] = useState<DatasetSummary[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [runError, setRunError] = useState<unknown>(null);
  const [runSubmitting, setRunSubmitting] = useState(false);
  // React may batch the final keystrokes before a following button click. Keeping the
  // current draft in a ref ensures template braces are not lost in that interaction.
  const valuesRef = useRef<EditorValues | null>(null);
  const modelEdited = useRef(false);

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
    valuesRef.current = null;
    modelEdited.current = false;
    setDetail(null);
    setValues(null);
    setSelectedId(null);
    setDraft(false);
  }, [agentId]);

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
    if (!empty || !config || modelEdited.current) return;
    const current = valuesRef.current;
    const next = {
      ...(current ?? blankValues()),
      model: setupStatus?.keys.find((key) => key.name === "gateway_api_key")?.set === false
        ? `local/${setupStatus.local_cli_default ?? setupStatus.local_cli_options[0] ?? "claude"}`
        : config.default_model,
    };
    valuesRef.current = next;
    setValues(next);
  }, [empty, config, setupStatus]);

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

  function updateSpecField(name: string, value: unknown): void {
    const spec = specObject(valuesRef.current?.spec ?? "");
    if (spec) updateSpec(JSON.stringify({ ...spec, [name]: value }, null, 2));
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
      modelEdited.current = true;
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
      setRunError(null);
      setRunOpen(true);
    } catch (err) {
      setError(err);
    }
  }

  async function runOverDataset(): Promise<void> {
    if (!selected || !datasetId || runSubmitting) return;
    setRunSubmitting(true);
    setRunError(null);
    try {
      const run = await runs.create({
        kind: "derive",
        version_id: selected.id,
        dataset_id: datasetId,
      });
      setRunOpen(false);
      navigate(`/runs/${run.id}`);
    } catch (err) {
      setRunError(err);
      setRunSubmitting(false);
    }
  }

  function closeRun(): void {
    if (runSubmitting) return;
    setRunOpen(false);
    setRunError(null);
  }

  if (loading && !detail) return <Spinner />;
  if (!detail)
    return <ErrorBanner error={error} onDismiss={() => setError(null)} />;
  if (!values) return <Spinner />;
  const readOnly = selected !== null && selected.frozen && !showDraft;
  const rows = values.deps_mapping;
  const parsedSpec = specObject(values.spec);
  const instructions = parsedSpec?.instructions;
  const gatewayEnabled = setupStatus?.keys.find((key) => key.name === "gateway_api_key")?.set !== false;
  const localModels = (setupStatus?.local_cli_options ?? ["claude", "codex", "cursor"]).map((name) => `local/${name}`);
  const modelOptions = [
    ...(values.model ? [values.model] : []),
    ...localModels,
    ...(gatewayEnabled ? config?.models ?? [] : []),
  ].filter((model, index, all) => all.indexOf(model) === index);

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
          <Select
            aria-label="Model"
            disabled={readOnly}
            value={values.model}
            options={[{ value: "", label: "Select a model…" }, ...modelOptions.map((model) => ({ value: model, label: model }))]}
            onChange={(event) => {
              modelEdited.current = true;
              updateValues({ model: event.target.value });
            }}
          />
        </label>
        <label className="field">
          <span className="field-label">Instructions</span>
          <TextArea
            aria-label="Instructions"
            readOnly={readOnly || !parsedSpec}
            rows={8}
            value={Array.isArray(instructions) ? instructions.join("\n\n") : String(instructions ?? "")}
            onChange={(event) => updateSpecField("instructions", event.target.value)}
          />
        </label>
        <label className="field">
          <span className="field-label">Input template (optional)</span>
          <TextArea
            aria-label="Prompt template"
            readOnly={readOnly}
            value={values.prompt_template}
            onChange={(event) =>
              updateValues({ prompt_template: event.target.value })
            }
          />
          <span className="muted">Leave blank to pass all input after the instructions.</span>
        </label>
        <label className="field">
          <span className="field-label">Input fields (optional)</span>
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
          <legend className="field-label">Variables mapping (optional)</legend>
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
        <div className="field">
          <span className="field-label">Harness capabilities</span>
          <p className="muted">Gateway agents can use multi-step planning, code, files, and shell capabilities. Local CLIs use their own tools.</p>
          <CapabilitiesEditor
            available={(config?.capabilities ?? []).filter((name) => name !== "SubAgents")}
            value={specCapabilities(parsedSpec)}
            readOnly={readOnly || !parsedSpec || values.model.startsWith("local/")}
            onChange={(capabilities) => updateSpecField("capabilities", capabilities.map(({ name, config }) => ({ [name]: config })))}
          />
        </div>
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
            <Button variant="secondary" onClick={() => setEvaluatorOpen(true)}>
              Create evaluator
            </Button>
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
      {selected && (
        <EvaluatorFromAgent
          key={selected.id}
          open={evaluatorOpen}
          version={selected}
          onGenerated={(generated) => navigate("/evaluators", { state: { draft: generated } })}
          onClose={() => setEvaluatorOpen(false)}
        />
      )}
      <Modal
        open={runOpen}
        title="Run agent over a dataset"
        onClose={closeRun}
        footer={
          <div className="form-actions">
            <Button
              variant="secondary"
              onClick={closeRun}
              disabled={runSubmitting}
            >
              Cancel
            </Button>
            <Button
              onClick={() => void runOverDataset()}
              disabled={!datasetId || runSubmitting}
            >
              {runSubmitting ? <Spinner /> : "Run agent"}
            </Button>
          </div>
        }
      >
        <ErrorBanner error={runError} onDismiss={() => setRunError(null)} />
        <label className="field">
          <span className="field-label">Dataset</span>
          <Select
            aria-label="Dataset"
            value={datasetId}
            disabled={runSubmitting}
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
