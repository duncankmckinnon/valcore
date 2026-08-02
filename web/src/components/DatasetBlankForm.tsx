// Blank flow: hand-author a dataset's name, columns, and label space with no rows yet.

import { useState } from "react";
import { datasets } from "../api/client";
import type { LabelSchema } from "../api/types";
import { Button, ErrorBanner, Spinner } from "./ui";
import LabelSchemaEditor from "./LabelSchemaEditor";

type DatasetBlankFormProps = { onCreated: (datasetId: string) => void };

const DEFAULT_SCHEMA: LabelSchema = { kind: "categorical", labels: [], minimum: null, maximum: null };

export default function DatasetBlankForm({ onCreated }: DatasetBlankFormProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [columnsText, setColumnsText] = useState("");
  const [schema, setSchema] = useState<LabelSchema>(DEFAULT_SCHEMA);
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const columns = columnsText
    .split(",")
    .map((column) => column.trim())
    .filter(Boolean);
  const canSubmit = name.trim() !== "" && columns.length > 0;

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      const dataset = await datasets.create({
        name: name.trim(),
        description: description.trim(),
        columns,
        label_schema: schema,
      });
      onCreated(dataset.id);
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="blank-form">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <label className="field">
        <span className="field-label">Name</span>
        <input className="select" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        <span className="field-label">Description</span>
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
      <LabelSchemaEditor value={schema} onChange={setSchema} />
      <div className="form-actions">
        <Button onClick={submit} disabled={submitting || !canSubmit}>
          {submitting ? <Spinner /> : "Create"}
        </Button>
      </div>
    </div>
  );
}
