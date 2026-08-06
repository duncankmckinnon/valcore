// Generate flow: describe the data and label space, then ask the model to synthesise rows.
// `description` is stored on the dataset; `instructions` steer generation and the server
// falls back to `description` as the prompt when they are absent.

import { useState } from "react";
import { datasets } from "../api/client";
import type { LabelSchema } from "../api/types";
import { Button, ErrorBanner, Spinner } from "./ui";
import LabelSchemaEditor from "./LabelSchemaEditor";
import ColumnNotesEditor from "./ColumnNotesEditor";
import LabelMixEditor from "./LabelMixEditor";
import { TOTAL_PERCENT, toProportions, totalPercent } from "./labelMix";
import type { LabelMixPercents } from "./labelMix";

type DatasetGenerateFormProps = { onCreated: (datasetId: string) => void };

const DEFAULT_SCHEMA: LabelSchema = { kind: "categorical", labels: [], minimum: null, maximum: null };

export default function DatasetGenerateForm({ onCreated }: DatasetGenerateFormProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const [columnsText, setColumnsText] = useState("");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [count, setCount] = useState(20);
  const [schema, setSchema] = useState<LabelSchema>(DEFAULT_SCHEMA);
  const [mixEnabled, setMixEnabled] = useState(false);
  const [mixPercents, setMixPercents] = useState<LabelMixPercents>({});
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const columns = columnsText
    .split(",")
    .map((column) => column.trim())
    .filter(Boolean);
  // A mix needs a categorical space; the API rejects one for numeric bounds.
  const mixLabels = schema.kind === "categorical" ? (schema.labels ?? []) : [];
  // Only block on the total while the mix is actually in play, and only when there are
  // labels to distribute over — otherwise the editor is not rendered at all.
  const mixActive = mixEnabled && mixLabels.length > 0;
  const mixIncomplete = mixActive && totalPercent(mixPercents) !== TOTAL_PERCENT;
  const canSubmit =
    name.trim() !== "" && description.trim() !== "" && columns.length > 0 && !mixIncomplete;

  // The columns text field owns the column set, so a note can outlive the column it was
  // written for — retyping the list is enough to orphan one. Notes are kept in state
  // (deleting a column by mistake should not lose its note) and pruned here instead: an
  // orphan would fail the server's `column_notes` check for a column the user cannot see.
  // Blank notes are dropped too, since they steer nothing.
  const columnNotes = Object.fromEntries(
    columns.filter((column) => (notes[column] ?? "").trim() !== "").map((column) => [
      column,
      notes[column].trim(),
    ]),
  );

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
        // Omitted when blank so the server falls back to using `description` as the
        // prompt, which is what this form did before it had an instructions box.
        ...(instructions.trim() !== "" ? { instructions: instructions.trim() } : {}),
        ...(Object.keys(columnNotes).length > 0 ? { column_notes: columnNotes } : {}),
        // Omitted unless prescribed, which leaves the distribution to the prompt.
        ...(mixActive ? { label_mix: toProportions(mixPercents) } : {}),
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
        <span className="field-label">Description</span>
        <textarea
          className="textarea"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>
      <label className="field">
        <span className="field-label">Instructions</span>
        <textarea
          className="textarea"
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
        />
      </label>
      {/* Outside the label: text inside it would become part of the field's accessible name. */}
      <p className="field-hint">
        How to generate the rows — content, difficulty, mix of cases. Leave blank to generate
        from the description alone.
      </p>
      <label className="field">
        <span className="field-label">Columns (comma separated)</span>
        <input
          className="select"
          value={columnsText}
          placeholder="question, answer"
          onChange={(e) => setColumnsText(e.target.value)}
        />
      </label>
      {columns.length > 0 && (
        <div className="field">
          <span className="field-label">Column notes (optional)</span>
          {/* The text field above owns the column set, so every column is locked here and
              none can be added — this editor only collects the per-column guidance. */}
          <ColumnNotesEditor
            lockedColumns={columns}
            extraColumns={[]}
            notes={notes}
            notePlaceholder="What should this column contain?"
            allowAddColumns={false}
            onChangeNotes={setNotes}
            onChangeExtraColumns={() => {}}
          />
        </div>
      )}
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
      <LabelMixEditor
        labels={mixLabels}
        percents={mixPercents}
        enabled={mixEnabled}
        count={count}
        onChangeEnabled={setMixEnabled}
        onChangePercents={setMixPercents}
      />
      <div className="form-actions">
        <Button onClick={submit} disabled={submitting || !canSubmit}>
          {submitting ? <Spinner /> : "Generate"}
        </Button>
      </div>
    </div>
  );
}
