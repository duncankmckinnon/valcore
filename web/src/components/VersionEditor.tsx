// The editable form for one evaluator version. A `null` version is an unsaved draft that
// starts blank and saves through `createVersion`; a real version edits in place, copying
// first when frozen so a frozen version is never mutated. `validateVersion` runs every
// render to surface inline errors and gate Save before the server ever sees the payload.
// The refine box and capability editors live in their own extracted components.

import { useEffect, useMemo, useState } from "react";
import { evaluators } from "../api/client";
import type {
  CapabilitySpec,
  EvaluatorVersion,
  GeneratedConfig,
  OutputField,
  ScoreKind,
} from "../api/types";
import { CapabilitiesEditor } from "./CapabilitiesEditor";
import { OutputFieldsEditor } from "./OutputFieldsEditor";
import { RefinePanel } from "./RefinePanel";
import { validateVersion } from "./versionValidation";
import type { VersionErrors } from "./versionValidation";
import { Badge, Button, ErrorBanner, Select, Spinner, TextArea } from "./ui";

export type AppConfig = {
  models: string[];
  tools: string[];
  capabilities: string[];
};

type FormState = {
  version_name: string;
  notes: string;
  model: string;
  instructions: string;
  prompt_template: string;
  required_columns: string[];
  output_fields: OutputField[];
  score_field: string;
  score_kind: ScoreKind;
  score_labels: string[] | null;
  score_minimum: number | null;
  score_maximum: number | null;
  capabilities: CapabilitySpec[];
  tools: string[];
};

type VersionEditorProps = {
  version: EvaluatorVersion | null;
  evaluatorId: string;
  config: AppConfig;
  evaluatorName?: string;
  initialDraft?: GeneratedConfig;
  onCreateDraft?: (version: Partial<EvaluatorVersion>) => Promise<EvaluatorVersion>;
  onSaved?: (version: EvaluatorVersion) => void;
};

function toForm(version: EvaluatorVersion): FormState {
  return {
    version_name: version.version_name,
    notes: version.notes,
    model: version.model,
    instructions: version.instructions,
    prompt_template: version.prompt_template,
    required_columns: [...version.required_columns],
    output_fields: version.output_fields.map((field) => ({ ...field })),
    score_field: version.score_field,
    score_kind: version.score_kind,
    score_labels: version.score_labels,
    score_minimum: version.score_minimum,
    score_maximum: version.score_maximum,
    capabilities: version.capabilities.map((capability) => ({ ...capability })),
    tools: [...version.tools],
  };
}

function blankForm(config: AppConfig): FormState {
  return {
    version_name: "",
    notes: "",
    model: config.models[0] ?? "",
    instructions: "",
    prompt_template: "",
    required_columns: [],
    output_fields: [],
    score_field: "",
    score_kind: "categorical",
    score_labels: null,
    score_minimum: null,
    score_maximum: null,
    capabilities: [],
    tools: [],
  };
}

function generatedForm(draft: GeneratedConfig, config: AppConfig): FormState {
  return {
    version_name: draft.version_name,
    notes: "",
    model: config.models[0] ?? "",
    instructions: draft.instructions,
    prompt_template: draft.prompt_template,
    required_columns: [...draft.required_columns],
    output_fields: draft.output_fields.map((field) => ({ ...field })),
    score_field: draft.score_field,
    score_kind: draft.score_kind,
    score_labels: draft.score_labels,
    score_minimum: draft.score_minimum,
    score_maximum: draft.score_maximum,
    capabilities: draft.capabilities.map((capability) => ({ ...capability })),
    tools: [...draft.tools],
  };
}

/** Output fields whose type is compatible with a given score kind. */
function compatibleFields(fields: OutputField[], kind: ScoreKind): OutputField[] {
  if (kind === "categorical") {
    return fields.filter((field) => field.type === "enum");
  }
  return fields.filter((field) => field.type === "int" || field.type === "float");
}

// Keep score_labels equal to the categorical score field's enum_values (numeric drops them),
// so a config the user assembles through the form stays server-valid without a hidden knob.
function syncScoreLabels(form: FormState): FormState {
  if (form.score_kind !== "categorical") {
    return { ...form, score_labels: null };
  }
  const field = form.output_fields.find((f) => f.name === form.score_field);
  if (field && field.type === "enum") {
    return { ...form, score_labels: field.enum_values };
  }
  return form;
}

export function VersionEditor({
  version,
  evaluatorId,
  config,
  evaluatorName,
  initialDraft,
  onCreateDraft,
  onSaved,
}: VersionEditorProps) {
  const [form, setForm] = useState<FormState>(() =>
    version ? toForm(version) : initialDraft ? generatedForm(initialDraft, config) : blankForm(config),
  );
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [columnDraft, setColumnDraft] = useState("");

  useEffect(() => {
    setForm(
      version ? toForm(version) : initialDraft ? generatedForm(initialDraft, config) : blankForm(config),
    );
    setError(null);
    // config is stable for the lifetime of an editor; only a version swap resets the form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, initialDraft]);

  const frozen = version?.frozen ?? false;
  const errors = validateVersion(form);
  const hasErrors = Object.keys(errors).length > 0;

  const scoreFieldOptions = useMemo(
    () => compatibleFields(form.output_fields, form.score_kind),
    [form.output_fields, form.score_kind],
  );

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const setScoreKind = (kind: ScoreKind) => {
    setForm((prev) => {
      const compatible = compatibleFields(prev.output_fields, kind);
      const score_field = compatible.some((field) => field.name === prev.score_field)
        ? prev.score_field
        : (compatible[0]?.name ?? "");
      return syncScoreLabels({ ...prev, score_kind: kind, score_field });
    });
  };

  const toggleTool = (name: string, enabled: boolean) => {
    setForm((prev) => ({
      ...prev,
      tools: enabled ? [...prev.tools, name] : prev.tools.filter((tool) => tool !== name),
    }));
  };

  const addColumn = () => {
    const value = columnDraft.trim();
    if (value === "" || form.required_columns.includes(value)) {
      setColumnDraft("");
      return;
    }
    update("required_columns", [...form.required_columns, value]);
    setColumnDraft("");
  };

  const removeColumn = (name: string) => {
    update(
      "required_columns",
      form.required_columns.filter((column) => column !== name),
    );
  };

  const asConfig = (): GeneratedConfig => ({
    name: evaluatorName ?? "",
    version_name: form.version_name,
    instructions: form.instructions,
    prompt_template: form.prompt_template,
    required_columns: form.required_columns,
    output_fields: form.output_fields,
    score_field: form.score_field,
    score_kind: form.score_kind,
    score_labels: form.score_labels,
    score_minimum: form.score_minimum,
    score_maximum: form.score_maximum,
    capabilities: form.capabilities,
    tools: form.tools,
    rationale: "",
  });

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    const patch: Partial<EvaluatorVersion> = { ...form };
    try {
      let result: EvaluatorVersion;
      if (version === null) {
        result = onCreateDraft
          ? await onCreateDraft(patch)
          : await evaluators.createVersion(evaluatorId, patch);
      } else {
        let target = version;
        if (frozen) {
          target = await evaluators.copyVersion(evaluatorId, version.id);
        }
        result = await evaluators.updateVersion(evaluatorId, target.id, patch);
      }
      onSaved?.(result);
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  };

  const applyRefine = (updates: Partial<GeneratedConfig>) => {
    setForm((prev) => ({ ...prev, ...updates }));
  };

  const fieldError = (key: keyof VersionErrors) =>
    errors[key] ? (
      <span className="field-error" role="alert">
        {errors[key]}
      </span>
    ) : null;

  const saveLabel = version === null ? "Create version" : frozen ? "Save as new version" : "Save changes";

  return (
    <div className="version-editor">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {frozen && (
        <p className="version-editor-frozen">
          <Badge tone="warning">Frozen</Badge> This version is read-only. Editing saves a new
          version.
        </p>
      )}

      <label className="field">
        <span className="field-label">Version name</span>
        <input
          className="input"
          aria-label="Version name"
          value={form.version_name}
          readOnly={frozen}
          onChange={(event) => update("version_name", event.target.value)}
        />
        {fieldError("version_name")}
      </label>

      <label className="field">
        <span className="field-label">Model</span>
        <Select
          aria-label="Model"
          disabled={frozen}
          value={form.model}
          options={config.models.map((model) => ({ value: model, label: model }))}
          onChange={(event) => update("model", event.target.value)}
        />
        {fieldError("model")}
      </label>

      <label className="field">
        <span className="field-label">Instructions</span>
        <TextArea
          aria-label="Instructions"
          className="instructions"
          rows={12}
          value={form.instructions}
          readOnly={frozen}
          onChange={(event) => update("instructions", event.target.value)}
        />
        {fieldError("instructions")}
      </label>

      <label className="field">
        <span className="field-label">Prompt template</span>
        <TextArea
          aria-label="Prompt template"
          value={form.prompt_template}
          readOnly={frozen}
          onChange={(event) => update("prompt_template", event.target.value)}
        />
        {fieldError("prompt_template")}
      </label>

      <div className="field">
        <span className="field-label">Required columns</span>
        <div className="chips">
          {form.required_columns.map((column) => (
            <span key={column} className="chip">
              {column}
              {!frozen && (
                <button
                  type="button"
                  className="chip-remove"
                  aria-label={`Remove column ${column}`}
                  onClick={() => removeColumn(column)}
                >
                  ×
                </button>
              )}
            </span>
          ))}
        </div>
        {!frozen && (
          <div className="chip-add">
            <input
              className="input"
              aria-label="Add required column"
              value={columnDraft}
              onChange={(event) => setColumnDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  addColumn();
                }
              }}
            />
            <Button variant="secondary" onClick={addColumn}>
              Add
            </Button>
          </div>
        )}
        {fieldError("required_columns")}
      </div>

      <div className="field">
        <span className="field-label">Output fields</span>
        <OutputFieldsEditor
          fields={form.output_fields}
          readOnly={frozen}
          onChange={(fields) => setForm((prev) => syncScoreLabels({ ...prev, output_fields: fields }))}
        />
        {fieldError("output_fields")}
      </div>

      <label className="field">
        <span className="field-label">Score kind</span>
        <Select
          aria-label="Score kind"
          disabled={frozen}
          value={form.score_kind}
          options={[
            { value: "categorical", label: "categorical" },
            { value: "numeric", label: "numeric" },
          ]}
          onChange={(event) => setScoreKind(event.target.value as ScoreKind)}
        />
        {fieldError("score_kind")}
      </label>

      <label className="field">
        <span className="field-label">Score field</span>
        <Select
          aria-label="Score field"
          disabled={frozen}
          value={form.score_field}
          options={scoreFieldOptions.map((field) => ({ value: field.name, label: field.name }))}
          onChange={(event) =>
            setForm((prev) => syncScoreLabels({ ...prev, score_field: event.target.value }))
          }
        />
        {fieldError("score_field")}
        {fieldError("score_labels")}
      </label>

      <div className="field">
        <span className="field-label">Capabilities</span>
        <CapabilitiesEditor
          available={config.capabilities}
          value={form.capabilities}
          readOnly={frozen}
          onChange={(capabilities) => update("capabilities", capabilities)}
        />
      </div>

      <div className="field">
        <span className="field-label">Tools</span>
        <div className="tools">
          {config.tools.map((name) => (
            <label key={name} className="tool-toggle">
              <input
                type="checkbox"
                checked={form.tools.includes(name)}
                disabled={frozen}
                onChange={(event) => toggleTool(name, event.target.checked)}
              />
              {name}
            </label>
          ))}
        </div>
      </div>

      <div className="version-editor-actions">
        <Button variant="primary" onClick={handleSave} disabled={saving || hasErrors}>
          {saving ? <Spinner /> : saveLabel}
        </Button>
      </div>

      <RefinePanel config={asConfig()} onApply={applyRefine} />
    </div>
  );
}

export default VersionEditor;
