// Top-up flow: generate more rows into a dataset that already has some. The dataset's own
// columns and label space always apply, so the new rows stay compatible with the existing
// ones — this form only steers content, and every field is prefilled from the dataset's
// stored generation settings so repeating a previous ask needs only a count.

import { useEffect, useState } from "react";
import { datasets } from "../api/client";
import type { Dataset, DatasetGeneration, LabelSchema, RowsGenerate } from "../api/types";
import ColumnNotesEditor from "./ColumnNotesEditor";
import LabelMixEditor from "./LabelMixEditor";
import { TOTAL_PERCENT, fromProportions, toProportions, totalPercent } from "./labelMix";
import type { LabelMixPercents } from "./labelMix";
import { Button, ErrorBanner, Modal, Spinner } from "./ui";

type GenerateMoreRowsProps = {
  open: boolean;
  dataset: Dataset;
  generation: DatasetGeneration | null;
  maxCount: number;
  onGenerated: (added: number) => void;
  onClose: () => void;
};

const DEFAULT_COUNT = 20;

export default function GenerateMoreRows({
  open,
  dataset,
  generation,
  maxCount,
  onGenerated,
  onClose,
}: GenerateMoreRowsProps) {
  const [count, setCount] = useState(DEFAULT_COUNT);
  const [instructions, setInstructions] = useState("");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [mixEnabled, setMixEnabled] = useState(false);
  const [mixPercents, setMixPercents] = useState<LabelMixPercents>({});
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  // Prefill when the modal opens rather than on mount: the settings are fetched by the
  // parent and may arrive after this component first renders.
  useEffect(() => {
    if (!open) return;
    setCount(generation?.count ?? DEFAULT_COUNT);
    setInstructions(generation?.instructions ?? "");
    setNotes(generation?.column_notes ?? {});
    const percents = fromProportions(generation?.label_mix ?? null);
    setMixPercents(percents);
    setMixEnabled(Object.keys(percents).length > 0);
    setError(null);
  }, [open, generation]);

  // The label space is the dataset's own. An empty schema is the legal "no ground truth"
  // state, which has no labels to distribute over and no guidance to give.
  const schema = dataset.label_schema as LabelSchema;
  const hasLabelSpace = Object.keys(dataset.label_schema).length > 0;
  const mixLabels = hasLabelSpace && schema.kind === "categorical" ? (schema.labels ?? []) : [];
  const mixActive = mixEnabled && mixLabels.length > 0;
  const mixIncomplete = mixActive && totalPercent(mixPercents) !== TOTAL_PERCENT;

  const countExceeds = count > maxCount;
  const canSubmit = count >= 1 && !countExceeds && !mixIncomplete;

  // Notes are pruned to the dataset's columns and to non-blank values: a stored note for a
  // column since removed by an edit would fail the server's check for an unknown column.
  const columnNotes = Object.fromEntries(
    dataset.columns
      .filter((column) => (notes[column] ?? "").trim() !== "")
      .map((column) => [column, notes[column].trim()]),
  );

  async function submit() {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const payload: RowsGenerate = { count };
      if (instructions.trim() !== "") payload.instructions = instructions.trim();
      if (Object.keys(columnNotes).length > 0) payload.column_notes = columnNotes;
      if (mixActive) payload.label_mix = toProportions(mixPercents);
      const added = await datasets.generateRows(dataset.id, payload);
      onGenerated(added.length);
    } catch (err) {
      // Keep the form filled in so the user can correct and retry.
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open={open} title="Generate more rows" onClose={onClose}>
      <div className="generate-form">
        <ErrorBanner error={error} onDismiss={() => setError(null)} />

        <p className="field-hint">
          New rows use this dataset&apos;s existing columns and label space, so they stay
          compatible with the rows already here.
        </p>

        <label className="field">
          <span className="field-label">Rows to add</span>
          <input
            className="select"
            type="number"
            min={1}
            max={maxCount}
            value={count}
            onChange={(e) => setCount(Number(e.target.value))}
          />
        </label>

        {countExceeds && (
          <p className="destructive-warning">Rows to add must be {maxCount} or fewer.</p>
        )}

        <div className="field-group">
          <label className="field">
            <span className="field-label">Instructions</span>
            <textarea
              className="textarea"
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
            />
          </label>
          <p className="field-hint">
            Prefilled from how this dataset was generated. Leave blank to generate from its
            description.
          </p>
        </div>

        <div className="field">
          <span className="field-label">Column notes (optional)</span>
          <ColumnNotesEditor
            lockedColumns={dataset.columns}
            extraColumns={[]}
            notes={notes}
            notePlaceholder="What should this column contain?"
            allowAddColumns={false}
            onChangeNotes={setNotes}
            onChangeExtraColumns={() => {}}
          />
        </div>

        <LabelMixEditor
          labels={mixLabels}
          percents={mixPercents}
          enabled={mixEnabled}
          count={count}
          onChangeEnabled={setMixEnabled}
          onChangePercents={setMixPercents}
        />

        <div className="form-actions">
          <Button variant="secondary" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={submitting || !canSubmit}>
            {submitting ? <Spinner /> : "Generate"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
