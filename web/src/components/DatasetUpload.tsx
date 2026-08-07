// Upload flow: pick a CSV/JSONL file, preview the first rows parsed client-side,
// optionally mark a label column, edit the label schema, then POST to the API.

import { useMemo, useState } from "react";
import { datasets } from "../api/client";
import type { DatasetCreated, LabelSchema } from "../api/types";
import { parseFile, type ParsedFile } from "./parse";
import { Button, ErrorBanner, Select, Spinner } from "./ui";
import { Tooltip } from "./Tooltip";
import LabelSchemaEditor from "./LabelSchemaEditor";

type Props = {
  onCreated: (created: DatasetCreated) => void;
};

const PREVIEW_ROWS = 5;
const NONE = "";

const DEFAULT_SCHEMA: LabelSchema = { kind: "categorical", labels: [], minimum: null, maximum: null };

export default function DatasetUpload({ onCreated }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [parsed, setParsed] = useState<ParsedFile | null>(null);
  const [labelColumn, setLabelColumn] = useState<string>(NONE);
  const [schema, setSchema] = useState<LabelSchema>(DEFAULT_SCHEMA);
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const dataColumns = useMemo(
    () => (parsed ? parsed.columns.filter((column) => column !== labelColumn) : []),
    [parsed, labelColumn],
  );

  async function onFileChange(selected: File | null) {
    setFile(selected);
    setParsed(null);
    setLabelColumn(NONE);
    setError(null);
    if (!selected) return;
    if (!name) setName(selected.name.replace(/\.[^.]+$/, ""));
    try {
      const text = await selected.text();
      setParsed(parseFile(selected.name, text));
    } catch (err) {
      setError(err);
    }
  }

  async function onSubmit() {
    if (!file) return;
    setSubmitting(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("name", name.trim() || file.name);
      if (labelColumn !== NONE) form.append("label_column", labelColumn);
      form.append("label_schema", JSON.stringify(schema));
      const created = await datasets.upload(form);
      onCreated(created);
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  const labelOptions = [
    { value: NONE, label: "None" },
    ...(parsed?.columns ?? []).map((column) => ({ value: column, label: column })),
  ];

  return (
    <div className="dataset-upload">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <p className="field-hint">
        Upload a CSV or JSONL file. The header row (or each record&apos;s keys) names the
        columns; every other row becomes a dataset row.
      </p>

      <label className="field">
        <span className="field-label">Name</span>
        <input
          className="select"
          value={name}
          placeholder="Dataset name"
          onChange={(e) => setName(e.target.value)}
        />
      </label>

      <label className="field">
        <span className="field-label">File (CSV or JSONL)</span>
        <input
          type="file"
          accept=".csv,.jsonl,.json"
          aria-label="File (CSV or JSONL)"
          onChange={(e) => onFileChange(e.target.files?.[0] ?? null)}
        />
      </label>

      {parsed && (
        <>
          <label className="field">
            <span className="field-label">
              Label column (optional)
              <Tooltip text="The column holding each row's ground-truth label. Naming it here stores those values as labels rather than as ordinary data columns." />
            </span>
            <Select
              options={labelOptions}
              value={labelColumn}
              aria-label="Label column"
              onChange={(e) => setLabelColumn(e.target.value)}
            />
          </label>

          <div className="upload-preview">
            <div className="field-label">
              Preview — {dataColumns.length} column{dataColumns.length === 1 ? "" : "s"},{" "}
              {parsed.rows.length} row{parsed.rows.length === 1 ? "" : "s"}
            </div>
            <table className="table">
              <thead>
                <tr>
                  {dataColumns.map((column) => (
                    <th key={column}>{column}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {parsed.rows.slice(0, PREVIEW_ROWS).map((row, index) => (
                  <tr key={index}>
                    {dataColumns.map((column) => (
                      <td key={column}>{row[column]}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <LabelSchemaEditor value={schema} onChange={setSchema} />
        </>
      )}

      <div className="form-actions">
        <Button onClick={onSubmit} disabled={!file || submitting}>
          {submitting ? <Spinner /> : "Upload"}
        </Button>
      </div>
    </div>
  );
}
