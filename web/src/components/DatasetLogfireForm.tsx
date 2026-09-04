// Logfire create flow: SQL in, sampled top-level trees out. The workbench and traces
// view open in a new tab from the read key's project; copy-back is paste into the
// textarea. Pull needs the Logfire read key, which is set on Settings — this form never
// asks for a secret.

import { useState } from "react";
import { datasets } from "../api/client";
import type { LabelSchema } from "../api/types";
import { Button, ErrorBanner, Spinner } from "./ui";
import { Tooltip } from "./Tooltip";
import { FormFooter } from "./FormFooter";
import { useSetup } from "./useSetup";
import LabelSchemaEditor from "./LabelSchemaEditor";

type Props = { onCreated: (datasetId: string) => void };

const DEFAULT_SCHEMA: LabelSchema = {
  kind: "categorical",
  labels: [],
  minimum: null,
  maximum: null,
};
const DEFAULT_COUNT = 20;
const SET_KEY = "Set the Logfire read key on the Settings page to pull.";

export default function DatasetLogfireForm({ onCreated }: Props) {
  const { status, loading, error: setupError } = useSetup();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [sql, setSql] = useState("");
  const [count, setCount] = useState(DEFAULT_COUNT);
  const [seed, setSeed] = useState("");
  const [minTimestamp, setMinTimestamp] = useState("");
  const [maxTimestamp, setMaxTimestamp] = useState("");
  const [labelColumn, setLabelColumn] = useState("");
  const [schema, setSchema] = useState<LabelSchema>(DEFAULT_SCHEMA);
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const logfireKeySet = status?.keys.some((key) => key.name === "logfire_read_key" && key.set);
  const logfireReady = loading || setupError !== null || logfireKeySet !== false;
  const exploreUrl = status?.logfire_explore_url ?? null;
  const tracesUrl = status?.logfire_traces_url ?? null;

  const blockers: string[] = [];
  if (!logfireReady) blockers.push("Set the Logfire read key on the Settings page to pull.");
  if (name.trim() === "") blockers.push("Name the dataset.");
  if (sql.trim() === "") blockers.push("Write a SQL query, or paste one from the workbench.");
  if (count < 1) blockers.push("Sample at least one top-level entry.");
  if (labelColumn.trim() !== "" && (schema.labels?.length ?? 0) === 0 && schema.kind === "categorical") {
    blockers.push("A label column needs a label schema.");
  }

  async function pasteSql() {
    try {
      const text = await navigator.clipboard.readText();
      if (text) setSql(text);
    } catch {
      // Permission denied: the textarea is still there to paste into by hand.
    }
  }

  async function submit() {
    if (blockers.length > 0) return;
    setSubmitting(true);
    setError(null);
    try {
      const seedValue = seed.trim() === "" ? undefined : Number(seed);
      const created = await datasets.fromLogfire({
        name: name.trim(),
        description: description.trim(),
        sql: sql.trim(),
        sample_n: count,
        ...(seedValue !== undefined && !Number.isNaN(seedValue) ? { seed: seedValue } : {}),
        ...(minTimestamp.trim() !== "" ? { min_timestamp: minTimestamp.trim() } : {}),
        ...(maxTimestamp.trim() !== "" ? { max_timestamp: maxTimestamp.trim() } : {}),
        ...(labelColumn.trim() !== ""
          ? { label_column: labelColumn.trim(), label_schema: schema }
          : {}),
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
      <p className="field-hint">
        SQL decides which records are pulled. Child spans in the result nest under their
        parents; then we sample top-level entries at random.
      </p>
      <label className="field">
        <span className="field-label">Name</span>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        <span className="field-label">Description</span>
        <textarea
          className="textarea"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>
      <div className="field">
        <span className="field-label">
          SQL
          <Tooltip
            text="Write the query in Logfire’s SQL Workbench for schema hints, then paste it back here."
            label="About SQL"
          />
        </span>
        <div className="form-actions" style={{ marginBottom: "0.5rem" }}>
          {exploreUrl ? (
            <a
              className="btn btn-secondary"
              href={exploreUrl}
              target="_blank"
              rel="noreferrer"
            >
              Open SQL Workbench
            </a>
          ) : (
            <Button variant="secondary" disabled title={SET_KEY}>
              Open SQL Workbench
            </Button>
          )}
          {tracesUrl ? (
            <a
              className="btn btn-secondary"
              href={tracesUrl}
              target="_blank"
              rel="noreferrer"
            >
              Open traces
            </a>
          ) : null}
          <Button variant="secondary" onClick={() => void pasteSql()}>
            Paste
          </Button>
        </div>
        <textarea
          className="textarea"
          aria-label="SQL"
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          rows={8}
          spellCheck={false}
        />
      </div>
      <label className="field">
        <span className="field-label">Sample size</span>
        <input
          className="input"
          type="number"
          min={1}
          value={count}
          onChange={(e) => setCount(Number(e.target.value))}
        />
      </label>
      <label className="field">
        <span className="field-label">Seed (optional)</span>
        <input
          className="input"
          value={seed}
          onChange={(e) => setSeed(e.target.value)}
          placeholder="generated if omitted"
        />
      </label>
      <label className="field">
        <span className="field-label">Min timestamp (optional, ISO-8601)</span>
        <input
          className="input"
          value={minTimestamp}
          onChange={(e) => setMinTimestamp(e.target.value)}
          placeholder="defaults to 24 hours ago"
        />
      </label>
      <label className="field">
        <span className="field-label">Max timestamp (optional, ISO-8601)</span>
        <input
          className="input"
          value={maxTimestamp}
          onChange={(e) => setMaxTimestamp(e.target.value)}
        />
      </label>
      <label className="field">
        <span className="field-label">Label column (optional)</span>
        <input
          className="input"
          value={labelColumn}
          onChange={(e) => setLabelColumn(e.target.value)}
          placeholder="SQL column to use as the label"
        />
      </label>
      {labelColumn.trim() !== "" && <LabelSchemaEditor value={schema} onChange={setSchema} />}
      <FormFooter blockers={blockers}>
        <Button onClick={() => void submit()} disabled={submitting || blockers.length > 0}>
          {submitting ? <Spinner /> : "Pull dataset"}
        </Button>
      </FormFooter>
    </div>
  );
}
