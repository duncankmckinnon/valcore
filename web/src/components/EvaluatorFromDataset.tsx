// web/src/components/EvaluatorFromDataset.tsx
// Seeded generation, dataset -> evaluator direction. Given an existing dataset, derive an
// evaluator draft whose column set *is* the dataset's columns (the fixed, required set)
// while the user supplies criteria and per-column notes to steer the generated judge.
//
// The shape must come from the dataset alone: the server rejects an explicit `columns`
// array paired with `dataset_id`, so this modal sends `dataset_id` and `column_notes` and
// never a `columns` key. The result is an editable draft handed back through `onGenerated`;
// the modal persists nothing — it neither creates an evaluator nor saves a version.
//
// The label space comes from one of the dataset's label sets (mirroring
// `_resolve_seed` in routes/evaluators.py): none declared, exactly one (used
// automatically), or more than one (the user must pick, defaulting to the first).
// Checking "prescribe my own" always overrides with a custom LabelSchema instead,
// regardless of how many label sets exist.

import { useEffect, useState } from "react";
import { evaluators, labelSets as labelSetsApi } from "../api/client";
import type { Dataset, GeneratedConfig, LabelSchema, LabelSetProgress } from "../api/types";
import { Button, ErrorBanner, Modal, Select, TextArea } from "./ui";
import { ColumnNotesEditor } from "./ColumnNotesEditor";
import LabelSchemaEditor from "./LabelSchemaEditor";
import { GATEWAY_BLOCKER, useSetup } from "./useSetup";

type EvaluatorFromDatasetProps = {
  open: boolean;
  dataset: Dataset;
  onGenerated: (draft: GeneratedConfig) => void;
  onClose: () => void;
};

const DEFAULT_SCHEMA: LabelSchema = { kind: "categorical", labels: [], minimum: null, maximum: null };

export function EvaluatorFromDataset({
  open,
  dataset,
  onGenerated,
  onClose,
}: EvaluatorFromDatasetProps) {
  const [criteria, setCriteria] = useState("");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<string[]>(dataset.columns);
  const [availableLabelSets, setAvailableLabelSets] = useState<LabelSetProgress[] | null>(null);
  const [labelSetId, setLabelSetId] = useState<string>("");
  const [useDatasetLabels, setUseDatasetLabels] = useState(true);
  const [labelSchema, setLabelSchema] = useState<LabelSchema>(DEFAULT_SCHEMA);
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const { gatewayReady } = useSetup();

  useEffect(() => {
    if (!open) return;
    labelSetsApi
      .list(dataset.id)
      .then((sets) => {
        setAvailableLabelSets(sets);
        setLabelSetId(sets[0]?.id ?? "");
      })
      .catch(() => setAvailableLabelSets([]));
  }, [open, dataset.id]);

  const canSubmit =
    criteria.trim() !== "" && selected.length > 0 && !submitting && gatewayReady;

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const column_notes = Object.fromEntries(
        Object.entries(notes).filter(
          ([column, note]) => note.trim() !== "" && selected.includes(column),
        ),
      );
      const draft = await evaluators.generate({
        criteria: criteria.trim(),
        dataset_id: dataset.id,
        columns: selected,
        column_notes,
        ...(useDatasetLabels
          ? labelSetId
            ? { label_set_id: labelSetId }
            : {}
          : { label_schema: labelSchema }),
      });
      onGenerated(draft);
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  };

  const hasLabelSets = (availableLabelSets?.length ?? 0) > 0;
  const ambiguous = (availableLabelSets?.length ?? 0) > 1;

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
        {availableLabelSets === null ? null : hasLabelSets ? (
          <>
            <label className="field-inline">
              <input
                type="checkbox"
                checked={useDatasetLabels}
                onChange={(event) => setUseDatasetLabels(event.target.checked)}
              />
              Use one of this dataset&apos;s label sets
            </label>

            {useDatasetLabels ? (
              <>
                {ambiguous && (
                  <Select
                    aria-label="Label set"
                    value={labelSetId}
                    options={(availableLabelSets ?? []).map((set) => ({ value: set.id, label: set.name }))}
                    onChange={(e) => setLabelSetId(e.target.value)}
                  />
                )}
                <p className="muted">
                  The generated evaluator will use{" "}
                  {availableLabelSets?.find((s) => s.id === labelSetId)?.name ?? "this label set"}.
                </p>
              </>
            ) : (
              <>
                <LabelSchemaEditor value={labelSchema} onChange={setLabelSchema} />
                <p className="form-footer-blocker" role="status">
                  Prescribing a label space means this evaluator can score {dataset.name} but
                  cannot be validated against it, since validation compares its labels to a
                  matching label set on the dataset.
                </p>
              </>
            )}
          </>
        ) : (
          <p className="muted">
            This dataset has no label sets, so the generated evaluator will define its own.
          </p>
        )}
      </div>
    </Modal>
  );
}

export default EvaluatorFromDataset;
