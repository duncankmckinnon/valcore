// Generate flow: describe the data and label space, then ask the model to synthesise rows.

import { useState } from "react";
import { datasets } from "../api/client";
import type { LabelSchema } from "../api/types";
import { Button, ErrorBanner, Spinner } from "./ui";
import LabelSchemaEditor from "./LabelSchemaEditor";

type DatasetGenerateFormProps = { onCreated: (datasetId: string) => void };

const DEFAULT_SCHEMA: LabelSchema = { kind: "categorical", labels: [], minimum: null, maximum: null };

export default function DatasetGenerateForm({ onCreated }: DatasetGenerateFormProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [columnsText, setColumnsText] = useState("");
  const [count, setCount] = useState(20);
  const [schema, setSchema] = useState<LabelSchema>(DEFAULT_SCHEMA);
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const columns = columnsText
    .split(",")
    .map((column) => column.trim())
    .filter(Boolean);
  const canSubmit = name.trim() !== "" && description.trim() !== "" && columns.length > 0;

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      const created = await datasets.generate({
        name: name.trim(),
        description: description.trim(),
        columns,
        label_schema: schema,
        count,
      });
      onCreated(created.dataset.id);
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="generate-form">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <label className="field">
        <span className="field-label">Name</span>
        <input className="select" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        <span className="field-label">Description of the data to generate</span>
        <textarea
          className="textarea"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>
      <label className="field">
        <span className="field-label">Columns (comma separated)</span>
        <input
          className="select"
          value={columnsText}
          placeholder="question, answer"
          onChange={(e) => setColumnsText(e.target.value)}
        />
      </label>
      <label className="field">
        <span className="field-label">Row count</span>
        <input
          className="select"
          type="number"
          min={1}
          max={200}
          value={count}
          onChange={(e) => setCount(Number(e.target.value))}
        />
      </label>
      <LabelSchemaEditor value={schema} onChange={setSchema} />
      <div className="form-actions">
        <Button onClick={submit} disabled={submitting || !canSubmit}>
          {submitting ? <Spinner /> : "Generate"}
        </Button>
      </div>
    </div>
  );
}
