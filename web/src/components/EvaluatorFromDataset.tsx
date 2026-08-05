// Seeded generation, dataset -> evaluator direction. Given an existing dataset, derive an
// evaluator draft whose column set *is* the dataset's columns (the fixed, required set)
// while the user supplies criteria and per-column notes to steer the generated judge.
//
// The shape must come from the dataset alone: the server rejects an explicit `columns`
// array paired with `dataset_id`, so this modal sends `dataset_id` and `column_notes` and
// never a `columns` key. The result is an editable draft handed back through `onGenerated`;
// the modal persists nothing — it neither creates an evaluator nor saves a version.

import { useState } from "react";
import { evaluators } from "../api/client";
import type { Dataset, GeneratedConfig, LabelSchema } from "../api/types";
import { Button, ErrorBanner, Modal, TextArea } from "./ui";
import { ColumnNotesEditor } from "./ColumnNotesEditor";

type EvaluatorFromDatasetProps = {
  open: boolean;
  dataset: Dataset;
  onGenerated: (draft: GeneratedConfig) => void;
  onClose: () => void;
};

// The API represents "no ground truth" as a literal empty object. Presence of `kind`, not
// labels or bounds, distinguishes a declared schema (including an unbounded numeric one).
function declaresLabelSpace(schema: Dataset["label_schema"]): schema is LabelSchema {
  return Object.keys(schema).length > 0 && "kind" in schema;
}

export function EvaluatorFromDataset({
  open,
  dataset,
  onGenerated,
  onClose,
}: EvaluatorFromDatasetProps) {
  const [criteria, setCriteria] = useState("");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const schema = dataset.label_schema;
  const hasLabelSpace = declaresLabelSpace(schema);
  const canSubmit = criteria.trim() !== "" && !submitting;

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      // Only columns the user actually annotated travel as notes; blank rows carry no
      // steer. `dataset_id` fixes the column set, so we deliberately omit `columns`.
      const column_notes = Object.fromEntries(
        Object.entries(notes).filter(([, note]) => note.trim() !== ""),
      );
      const draft = await evaluators.generate({
        criteria: criteria.trim(),
        dataset_id: dataset.id,
        column_notes,
      });
      onGenerated(draft);
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal open={open} title="Generate evaluator" onClose={onClose}>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <label className="field">
        <span className="field-label">Criteria</span>
        <TextArea
          aria-label="Criteria"
          rows={6}
          placeholder="Describe what is being evaluated…"
          value={criteria}
          onChange={(event) => setCriteria(event.target.value)}
        />
      </label>

      <div className="field">
        <span className="field-label">Columns</span>
        <ColumnNotesEditor
          lockedColumns={dataset.columns}
          extraColumns={[]}
          notes={notes}
          notePlaceholder="How does this column factor into the assessment?"
          allowAddColumns={false}
          onChangeNotes={setNotes}
          onChangeExtraColumns={() => {
            // allowAddColumns is false, so the dataset's columns are the fixed set and the
            // editor never reports extra-column edits.
          }}
        />
      </div>

      <div className="field">
        <span className="field-label">Label space</span>
        {hasLabelSpace ? (
          <>
            {schema.kind === "categorical" ? (
              <div className="chips">
                {(schema.labels ?? []).map((label) => (
                  <span key={label} className="chip">
                    {label}
                  </span>
                ))}
              </div>
            ) : (
              <p>
                Minimum: {schema.minimum ?? "unbounded"}; Maximum:{" "}
                {schema.maximum ?? "unbounded"}
              </p>
            )}
            <p className="muted">
              The generated evaluator will use this dataset's label space.
            </p>
          </>
        ) : (
          <p className="muted">
            This dataset declares no label space, so the generated evaluator will define its
            own.
          </p>
        )}
      </div>

      <div className="modal-actions">
        <Button variant="secondary" onClick={onClose} disabled={submitting}>
          Cancel
        </Button>
        <Button variant="primary" onClick={submit} disabled={!canSubmit}>
          Generate evaluator
        </Button>
      </div>
    </Modal>
  );
}

export default EvaluatorFromDataset;
