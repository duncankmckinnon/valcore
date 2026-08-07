// Generate flow: describe the data and label space, then ask the model to synthesise rows.
// `description` is stored on the dataset; `instructions` steer generation and the server
// falls back to `description` as the prompt when they are absent.

import { useState } from "react";
import { datasets } from "../api/client";
import type { LabelSchema } from "../api/types";
import { Button, ErrorBanner, Spinner } from "./ui";
import { Tooltip } from "./Tooltip";
import { FormFooter } from "./FormFooter";
import LabelSchemaEditor from "./LabelSchemaEditor";
import ColumnNotesEditor from "./ColumnNotesEditor";
import LabelMixEditor from "./LabelMixEditor";
import { TOTAL_PERCENT, apportion, fromProportions, toProportions, totalPercent } from "./labelMix";
import type { LabelMixPercents } from "./labelMix";

/** Prefill for the form, e.g. seeded from an existing dataset's stored settings. */
export type GenerateFormInitial = {
  name?: string;
  description?: string;
  instructions?: string;
  columns?: string[];
  columnNotes?: Record<string, string>;
  labelSchema?: LabelSchema;
  labelMix?: Record<string, number> | null;
  count?: number;
};

type DatasetGenerateFormProps = {
  onCreated: (datasetId: string) => void;
  // Read once, at mount: callers change the prefill by remounting with a new `key`, which
  // keeps the fields plain state rather than props that must be synced back on every edit.
  initial?: GenerateFormInitial;
};

const DEFAULT_SCHEMA: LabelSchema = { kind: "categorical", labels: [], minimum: null, maximum: null };
const DEFAULT_COUNT = 20;

// The longer explanation that used to sit inline under the field. It steers the generated
// content and is optional, so it belongs in an on-demand tooltip rather than as permanent
// prose competing with the field for attention.
const INSTRUCTIONS_HINT =
  "How to generate the rows — content, difficulty, mix of cases. Leave blank to generate " +
  "from the description alone.";

export default function DatasetGenerateForm({ onCreated, initial }: DatasetGenerateFormProps) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [instructions, setInstructions] = useState(initial?.instructions ?? "");
  const [columnsText, setColumnsText] = useState((initial?.columns ?? []).join(", "));
  const [notes, setNotes] = useState<Record<string, string>>(initial?.columnNotes ?? {});
  const [count, setCount] = useState(initial?.count ?? DEFAULT_COUNT);
  const [schema, setSchema] = useState<LabelSchema>(initial?.labelSchema ?? DEFAULT_SCHEMA);
  const [initialPercents] = useState(() => fromProportions(initial?.labelMix ?? null));
  const [mixEnabled, setMixEnabled] = useState(Object.keys(initialPercents).length > 0);
  const [mixPercents, setMixPercents] = useState<LabelMixPercents>(initialPercents);
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

  // The old silent `canSubmit` becomes an ordered list of what is still missing, surfaced
  // through FormFooter one instruction at a time. The gating is unchanged: the button stays
  // disabled while any blocker remains, i.e. exactly when the old `canSubmit` was false.
  const blockers: string[] = [];
  if (name.trim() === "") blockers.push("Add a name");
  if (description.trim() === "") blockers.push("Add a description");
  if (columns.length === 0) blockers.push("Add at least one column");
  if (mixIncomplete) blockers.push("Label mix must total 100%");

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

  // Derived, not stored: the preview re-computes on every keystroke so the row shape and
  // apportioned counts always reflect the current fields. Row counts reuse the shared
  // apportionment helper so the preview and LabelMixEditor never disagree.
  const previewRows =
    mixActive && !mixIncomplete ? apportion(mixPercents, count) : {};

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
      <div className="modal-two-pane">
        {/* The grid's first child is the main column; the stylesheet targets `.modal-two-pane`
            and `.modal-side` directly, so this wrapper carries no class of its own. */}
        <div>
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
            {/* The tooltip trigger is a <button>, so it must stay out of any <label>: a
                button inside a label is treated as the field's control, and a button
                referenced through a label pollutes its accessible name. The label text is a
                plain span carrying the trigger, and the input names itself with `aria-label`
                so the trigger sits beside the label — where the spec wants it — for free. */}
            <span className="field-label">
              Instructions
              <Tooltip text={INSTRUCTIONS_HINT} label="About instructions" />
            </span>
            <textarea
              className="textarea"
              aria-label="Instructions"
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
            />
          </div>
          <div className="field">
            <span className="field-label">
              Columns (comma separated)
              <Tooltip
                text="The fields every generated row will carry. These become the dataset's columns."
                label="About columns"
              />
            </span>
            <input
              className="input"
              aria-label="Columns (comma separated)"
              value={columnsText}
              placeholder="question, answer"
              onChange={(e) => setColumnsText(e.target.value)}
            />
          </div>
          {columns.length > 0 && (
            <div className="field">
              <span className="field-label">
                Column notes (optional)
                <Tooltip
                  text="Optional guidance per column describing what it should contain. Blank notes steer nothing and are dropped."
                  label="About column notes"
                />
              </span>
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
          <div className="field">
            <span className="field-label">
              Row count
              <Tooltip text="How many rows to generate, from 1 to 200." label="About row count" />
            </span>
            <input
              className="input"
              aria-label="Row count"
              type="number"
              min={1}
              max={200}
              value={count}
              onChange={(e) => setCount(Number(e.target.value))}
            />
          </div>
          <LabelSchemaEditor value={schema} onChange={setSchema} />
          <LabelMixEditor
            labels={mixLabels}
            percents={mixPercents}
            enabled={mixEnabled}
            count={count}
            onChangeEnabled={setMixEnabled}
            onChangePercents={setMixPercents}
          />
        </div>
        <div className="modal-side">
          <div className="preview-pane">
            <pre className="preview-code">
              {"{\n"}
              {columns.map((column) => `  "column": "${column}"\n`).join("")}
              {mixLabels.length > 0 ? `  "label": "${mixLabels.join(" | ")}"\n` : ""}
              {"}"}
            </pre>
            {mixActive && (
              // Unclassed wrapper: the mix bars group under `.preview-pane`, which owns the
              // spacing, so no dedicated container class is defined in the stylesheet.
              <div>
                {mixLabels.map((label) => {
                  const percent = mixPercents[label] ?? 0;
                  return (
                    <div key={label} className="mix-bar-row">
                      <span className="mix-bar-label">{label}</span>
                      <span className="mix-bar-track">
                        <span className="mix-bar-fill" style={{ width: `${percent}%` }} />
                      </span>
                      {/* No `.mix-bar-count` exists; the apportioned count reuses the defined
                          `.mix-bar-label` so it inherits the same type treatment. */}
                      <span className="mix-bar-label">{previewRows[label] ?? 0} rows</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
      <FormFooter
        blockers={blockers}
        ready={
          <>
            Generate {count} rows
            {mixLabels.length > 0 ? ` across ${mixLabels.length} labels` : ""}
          </>
        }
      >
        <Button onClick={submit} disabled={blockers.length > 0 || submitting}>
          {submitting ? <Spinner /> : "Generate"}
        </Button>
      </FormFooter>
    </div>
  );
}
