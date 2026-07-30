// The editable form for one evaluator version. When the version is frozen every input is
// read-only and saving copies the version first (so a frozen version can never be mutated
// in place). A refine box below the form turns a free-text instruction into a ConfigDiff.

import { useEffect, useMemo, useState } from "react";
import { evaluators } from "../api/client";
import type {
  CapabilitySpec,
  EvaluatorVersion,
  GeneratedConfig,
  OutputField,
  ScoreKind,
} from "../api/types";
import { ConfigDiff } from "./ConfigDiff";
import { OutputFieldsEditor } from "./OutputFieldsEditor";
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
  version: EvaluatorVersion;
  config: AppConfig;
  evaluatorName?: string;
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

/** Output fields whose type is compatible with a given score kind. */
function compatibleFields(fields: OutputField[], kind: ScoreKind): OutputField[] {
  if (kind === "categorical") {
    return fields.filter((field) => field.type === "enum");
  }
  return fields.filter((field) => field.type === "int" || field.type === "float");
}

export function VersionEditor({ version, config, evaluatorName, onSaved }: VersionEditorProps) {
  const [form, setForm] = useState<FormState>(() => toForm(version));
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [refining, setRefining] = useState(false);
  const [diff, setDiff] = useState<{ config: GeneratedConfig; changed: string[] } | null>(null);
  const [columnDraft, setColumnDraft] = useState("");

  useEffect(() => {
    setForm(toForm(version));
    setDiff(null);
    setError(null);
  }, [version]);

  const frozen = version.frozen;
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
      return { ...prev, score_kind: kind, score_field };
    });
  };

  const capabilityConfig = (name: string): Record<string, unknown> | null => {
    const found = form.capabilities.find((capability) => capability.name === name);
    return found ? found.config : null;
  };

  const toggleCapability = (name: string, enabled: boolean) => {
    setForm((prev) => ({
      ...prev,
      capabilities: enabled
        ? [...prev.capabilities, { name, config: {} }]
        : prev.capabilities.filter((capability) => capability.name !== name),
    }));
  };

  const updateCapabilityConfig = (name: string, changes: Record<string, unknown>) => {
    setForm((prev) => ({
      ...prev,
      capabilities: prev.capabilities.map((capability) =>
        capability.name === name
          ? { ...capability, config: { ...capability.config, ...changes } }
          : capability,
      ),
    }));
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
      let target = version;
      if (frozen) {
        target = await evaluators.copyVersion(version.evaluator_id, version.id);
      }
      const result = await evaluators.updateVersion(target.evaluator_id, target.id, patch);
      onSaved?.(result);
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  };

  const handleRefine = async () => {
    if (instruction.trim() === "") {
      return;
    }
    setRefining(true);
    setError(null);
    try {
      const refined = await evaluators.refine({ config: asConfig(), instruction });
      setDiff({ config: refined.config, changed: refined.changed_fields });
    } catch (err) {
      setError(err);
    } finally {
      setRefining(false);
    }
  };

  const applyDiff = (updates: Partial<GeneratedConfig>) => {
    setForm((prev) => ({ ...prev, ...updates }));
  };

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
      </label>

      <label className="field">
        <span className="field-label">Prompt template</span>
        <TextArea
          aria-label="Prompt template"
          value={form.prompt_template}
          readOnly={frozen}
          onChange={(event) => update("prompt_template", event.target.value)}
        />
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
      </div>

      <div className="field">
        <span className="field-label">Output fields</span>
        <OutputFieldsEditor
          fields={form.output_fields}
          readOnly={frozen}
          onChange={(fields) => update("output_fields", fields)}
        />
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
      </label>

      <label className="field">
        <span className="field-label">Score field</span>
        <Select
          aria-label="Score field"
          disabled={frozen}
          value={form.score_field}
          options={scoreFieldOptions.map((field) => ({ value: field.name, label: field.name }))}
          onChange={(event) => update("score_field", event.target.value)}
        />
      </label>

      <div className="field">
        <span className="field-label">Capabilities</span>
        {config.capabilities.map((name) => {
          const capConfig = capabilityConfig(name);
          const enabled = capConfig !== null;
          return (
            <div key={name} className="capability">
              <label className="capability-toggle">
                <input
                  type="checkbox"
                  checked={enabled}
                  disabled={frozen}
                  onChange={(event) => toggleCapability(name, event.target.checked)}
                />
                {name}
              </label>
              {enabled && name === "FileSystem" && (
                <input
                  className="input"
                  aria-label="FileSystem root dir"
                  placeholder="root dir"
                  value={String(capConfig?.root_dir ?? "")}
                  readOnly={frozen}
                  onChange={(event) =>
                    updateCapabilityConfig(name, { root_dir: event.target.value })
                  }
                />
              )}
              {enabled && name === "Shell" && (
                <div className="capability-config">
                  <input
                    className="input"
                    aria-label="Shell allowed commands"
                    placeholder="allowed commands (comma separated)"
                    value={((capConfig?.allowed_commands as string[]) ?? []).join(", ")}
                    readOnly={frozen}
                    onChange={(event) =>
                      updateCapabilityConfig(name, {
                        allowed_commands: event.target.value
                          .split(",")
                          .map((command) => command.trim())
                          .filter((command) => command.length > 0),
                      })
                    }
                  />
                  <input
                    className="input"
                    type="number"
                    aria-label="Shell timeout"
                    placeholder="timeout (s)"
                    value={
                      capConfig && capConfig.default_timeout !== undefined
                        ? String(capConfig.default_timeout)
                        : ""
                    }
                    readOnly={frozen}
                    onChange={(event) =>
                      updateCapabilityConfig(name, {
                        default_timeout:
                          event.target.value === "" ? undefined : Number(event.target.value),
                      })
                    }
                  />
                </div>
              )}
            </div>
          );
        })}
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
        <Button variant="primary" onClick={handleSave} disabled={saving}>
          {saving ? <Spinner /> : frozen ? "Save as new version" : "Save changes"}
        </Button>
      </div>

      <div className="refine-box">
        <span className="field-label">Refine</span>
        <TextArea
          aria-label="Refine instruction"
          placeholder="Describe a change in plain language…"
          value={instruction}
          onChange={(event) => setInstruction(event.target.value)}
        />
        <Button variant="secondary" onClick={handleRefine} disabled={refining}>
          {refining ? <Spinner /> : "Refine"}
        </Button>
        {diff && (
          <ConfigDiff
            current={asConfig()}
            proposed={diff.config}
            changedFields={diff.changed}
            onApply={applyDiff}
          />
        )}
      </div>
    </div>
  );
}

export default VersionEditor;
