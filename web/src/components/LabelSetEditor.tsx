// Editor for a label set: its name/description, kind toggle, and either a list of
// categorical labels (each with its own criteria description, shown as reference text
// in the annotation UI) or numeric bounds. Deliberately separate from
// LabelSchemaEditor, which authors a bare `LabelSchema` with no per-label description
// and no set-level name/description.

import { useState } from "react";
import type { AnnotationLabel, LabelSetCreate, ScoreKind } from "../api/types";
import { Button, Select } from "./ui";

type Props = {
  value: LabelSetCreate;
  onChange: (next: LabelSetCreate) => void;
};

const KIND_OPTIONS = [
  { value: "categorical", label: "Categorical" },
  { value: "numeric", label: "Numeric" },
];

export default function LabelSetEditor({ value, onChange }: Props) {
  const [draftName, setDraftName] = useState("");
  const [draftDescription, setDraftDescription] = useState("");

  function setKind(kind: ScoreKind) {
    if (kind === value.kind) return;
    if (kind === "categorical") {
      onChange({ ...value, kind, labels: value.labels ?? [], minimum: null, maximum: null });
    } else {
      onChange({ ...value, kind, labels: null, minimum: value.minimum ?? 0, maximum: value.maximum ?? 1 });
    }
  }

  function addLabel() {
    const name = draftName.trim();
    if (!name) return;
    const labels = value.labels ?? [];
    if (labels.some((label) => label.name === name)) {
      setDraftName("");
      setDraftDescription("");
      return;
    }
    const next: AnnotationLabel = { name, description: draftDescription.trim() };
    onChange({ ...value, labels: [...labels, next] });
    setDraftName("");
    setDraftDescription("");
  }

  function removeLabel(name: string) {
    onChange({ ...value, labels: (value.labels ?? []).filter((label) => label.name !== name) });
  }

  return (
    <div className="label-set-editor">
      <label className="field">
        <span className="field-label">Name</span>
        <input
          className="select"
          value={value.name}
          onChange={(e) => onChange({ ...value, name: e.target.value })}
        />
      </label>

      <label className="field">
        <span className="field-label">Description</span>
        <textarea
          className="textarea"
          value={value.description ?? ""}
          onChange={(e) => onChange({ ...value, description: e.target.value })}
        />
      </label>

      <label className="field">
        <span className="field-label">Label kind</span>
        <Select
          options={KIND_OPTIONS}
          value={value.kind}
          onChange={(e) => setKind(e.target.value as ScoreKind)}
        />
      </label>

      {value.kind === "categorical" ? (
        <div className="field">
          <span className="field-label">Labels</span>
          <div className="label-chips">
            {(value.labels ?? []).map((label) => (
              <span key={label.name} className="badge" title={label.description || undefined}>
                {label.name}
                <button
                  type="button"
                  className="chip-remove"
                  aria-label={`Remove ${label.name}`}
                  onClick={() => removeLabel(label.name)}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
          <div className="label-add">
            <input
              className="select"
              aria-label="Label name"
              value={draftName}
              placeholder="Label name"
              onChange={(e) => setDraftName(e.target.value)}
            />
            <input
              className="select"
              aria-label="Label criteria"
              value={draftDescription}
              placeholder="What this label means"
              onChange={(e) => setDraftDescription(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addLabel();
                }
              }}
            />
            <Button type="button" variant="secondary" onClick={addLabel}>
              Add label
            </Button>
          </div>
        </div>
      ) : (
        <div className="label-bounds">
          <label className="field">
            <span className="field-label">Minimum</span>
            <input
              className="select"
              type="number"
              value={value.minimum ?? ""}
              onChange={(e) =>
                onChange({ ...value, minimum: e.target.value === "" ? null : Number(e.target.value) })
              }
            />
          </label>
          <label className="field">
            <span className="field-label">Maximum</span>
            <input
              className="select"
              type="number"
              value={value.maximum ?? ""}
              onChange={(e) =>
                onChange({ ...value, maximum: e.target.value === "" ? null : Number(e.target.value) })
              }
            />
          </label>
        </div>
      )}
    </div>
  );
}
