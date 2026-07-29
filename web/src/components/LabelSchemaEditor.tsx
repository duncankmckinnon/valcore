// Editor for a dataset's label schema, shared by the upload and generate flows.
// A kind toggle switches between editing a categorical label list and numeric bounds.

import { useState } from "react";
import type { LabelSchema, ScoreKind } from "../api/types";
import { Button, Select } from "./ui";

type Props = {
  value: LabelSchema;
  onChange: (schema: LabelSchema) => void;
};

const KIND_OPTIONS = [
  { value: "categorical", label: "Categorical" },
  { value: "numeric", label: "Numeric" },
];

export default function LabelSchemaEditor({ value, onChange }: Props) {
  const [draft, setDraft] = useState("");

  function setKind(kind: ScoreKind) {
    if (kind === value.kind) return;
    if (kind === "categorical") {
      onChange({ kind, labels: value.labels ?? [], minimum: null, maximum: null });
    } else {
      onChange({ kind, labels: null, minimum: value.minimum ?? 0, maximum: value.maximum ?? 1 });
    }
  }

  function addLabel() {
    const label = draft.trim();
    if (!label) return;
    const labels = value.labels ?? [];
    if (labels.includes(label)) {
      setDraft("");
      return;
    }
    onChange({ ...value, labels: [...labels, label] });
    setDraft("");
  }

  function removeLabel(label: string) {
    onChange({ ...value, labels: (value.labels ?? []).filter((l) => l !== label) });
  }

  return (
    <div className="label-schema-editor">
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
              <span key={label} className="badge">
                {label}
                <button
                  type="button"
                  className="chip-remove"
                  aria-label={`Remove ${label}`}
                  onClick={() => removeLabel(label)}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
          <div className="label-add">
            <input
              className="select"
              value={draft}
              placeholder="Add a label"
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addLabel();
                }
              }}
            />
            <Button type="button" variant="secondary" onClick={addLabel}>
              Add
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
