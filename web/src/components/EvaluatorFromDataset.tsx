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
import LabelSchemaEditor from "./LabelSchemaEditor";
import { GATEWAY_BLOCKER, useSetup } from "./useSetup";

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
  // Every column is exposed by default: narrowing is the deliberate act, and defaulting to all
  // preserves the behaviour this modal had before the subset could be chosen.
  const [selected, setSelected] = useState<string[]>(dataset.columns);
  // Checked by default: inheriting the dataset's label space is the behaviour this modal had,
  // and the one that keeps the evaluator validatable against this dataset.
  const [useDatasetLabels, setUseDatasetLabels] = useState(true);
  // Seeded from the dataset so unchecking starts from its labels rather than an empty editor.
  const [labelSchema, setLabelSchema] = useState<LabelSchema>(
    declaresLabelSpace(dataset.label_schema)
      ? dataset.label_schema
      : { kind: "categorical", labels: [], minimum: null, maximum: null },
  );
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const { gatewayReady } = useSetup();

  const schema = dataset.label_schema;
  const hasLabelSpace = declaresLabelSpace(schema);
  // An evaluator must require at least one column -- `validate_version` rejects an empty
  // `required_columns` -- so deselecting everything blocks rather than failing server-side.
  const canSubmit =
    criteria.trim() !== "" && selected.length > 0 && !submitting && gatewayReady;

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      // Only annotated *and still selected* columns travel as notes: the server rejects a note
      // keyed outside the resolved column set, so a note left behind by a deselected column
      // would fail the whole request.
      const column_notes = Object.fromEntries(
        Object.entries(notes).filter(
          ([column, note]) => note.trim() !== "" && selected.includes(column),
        ),
      );
      const draft = await evaluators.generate({
        criteria: criteria.trim(),
        dataset_id: dataset.id,
        // Narrows the dataset-derived set so the evaluator need not require every column the
        // dataset carries.
        columns: selected,
        column_notes,
        // Omitted while the checkbox is on, so the server seeds the label space from the
        // dataset; sent only as a deliberate override.
        ...(useDatasetLabels ? {} : { label_schema: labelSchema }),
      });
      onGenerated(draft);
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title="Generate evaluator"
      description="The column shape is derived from the dataset; your criteria and notes only steer the generated judge."
      onClose={onClose}
      footer={
        <div className="modal-actions">
          {!gatewayReady ? (
            <span className="form-footer-blocker">{GATEWAY_BLOCKER}</span>
          ) : selected.length === 0 ? (
            <span className="form-footer-blocker">Include at least one column.</span>
          ) : null}
          <Button variant="secondary" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button variant="primary" onClick={submit} disabled={!canSubmit}>
            Generate evaluator
          </Button>
        </div>
      }
    >
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
          lockedBadge="required"
          allowAddColumns={false}
          selectedColumns={selected}
          onChangeSelected={setSelected}
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
            <label className="field-inline">
              <input
                type="checkbox"
                checked={useDatasetLabels}
                onChange={(event) => setUseDatasetLabels(event.target.checked)}
              />
              Use this dataset&apos;s label space
            </label>

            {useDatasetLabels ? (
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
                  The generated evaluator will use this dataset&apos;s label space.
                </p>
              </>
            ) : (
              <>
                <LabelSchemaEditor value={labelSchema} onChange={setLabelSchema} />
                {/* Stated where the choice is made, not discovered when a run fails: a
                    differing label space is exactly what check_dataset_compatibility refuses
                    for a VALIDATION run. */}
                <p className="form-footer-blocker" role="status">
                  Prescribing a label space means this evaluator can score{" "}
                  {dataset.name} but cannot be validated against it, since validation compares
                  its labels to the dataset&apos;s.
                </p>
              </>
            )}
          </>
        ) : (
          <p className="muted">
            This dataset declares no label space, so the generated evaluator will define its
            own.
          </p>
        )}
      </div>

    </Modal>
  );
}

export default EvaluatorFromDataset;
