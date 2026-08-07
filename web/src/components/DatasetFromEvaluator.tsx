// Seeded generation, evaluator → dataset. This modal derives its column shape from an
// evaluator version: the version's `required_columns` are locked in and cannot be removed,
// so the generated rows always satisfy the judge's contract. The user only steers content
// (per-column notes, overall instructions) and, opt-in, how suggested labels are assigned —
// never the label set itself, which comes from the version's score space.

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { datasets } from "../api/client";
import type { DatasetGenerateFromVersion, EvaluatorVersion } from "../api/types";
import ColumnNotesEditor from "./ColumnNotesEditor";
import LabelMixEditor from "./LabelMixEditor";
import { TOTAL_PERCENT, toProportions, totalPercent } from "./labelMix";
import type { LabelMixPercents } from "./labelMix";
import { Button, ErrorBanner, Modal, Spinner } from "./ui";
import { Tooltip } from "./Tooltip";

type DatasetFromEvaluatorProps = {
  open: boolean;
  version: EvaluatorVersion;
  // The server caps generated row counts; the caller passes its known maximum so the
  // form can reject an over-large request before spending a slow generation call on it.
  maxCount: number;
  onClose: () => void;
};

const DEFAULT_COUNT = 20;

export default function DatasetFromEvaluator({
  open,
  version,
  maxCount,
  onClose,
}: DatasetFromEvaluatorProps) {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [extraColumns, setExtraColumns] = useState<string[]>([]);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [includeLabels, setIncludeLabels] = useState(true);
  const [labelGuidance, setLabelGuidance] = useState("");
  const [mixEnabled, setMixEnabled] = useState(false);
  const [mixPercents, setMixPercents] = useState<LabelMixPercents>({});
  const [count, setCount] = useState(DEFAULT_COUNT);
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  // The label space is the evaluator's, so a mix distributes over its score labels and is
  // available only for a categorical space — the API rejects a mix for numeric bounds.
  const mixLabels = version.score_kind === "categorical" ? (version.score_labels ?? []) : [];
  // A distribution over labels is meaningless with labels off, which the API rejects; the
  // editor is hidden in that case, so the flag must not survive into the payload either.
  const mixActive = mixEnabled && includeLabels && mixLabels.length > 0;
  const mixIncomplete = mixActive && totalPercent(mixPercents) !== TOTAL_PERCENT;

  // The full column set must be fixed before the generation call (`_valid_rows` compares
  // key sets by exact equality), and an over-count request would only fail server-side, so
  // both are blocked here rather than surfaced late.
  const countExceeds = count > maxCount;
  const canSubmit = name.trim() !== "" && !countExceeds && !mixIncomplete;

  async function submit() {
    if (!canSubmit) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const payload: DatasetGenerateFromVersion = {
        version_id: version.id,
        name: name.trim(),
        extra_columns: extraColumns,
        column_notes: notes,
        include_labels: includeLabels,
        count,
      };
      if (instructions.trim() !== "") {
        payload.instructions = instructions.trim();
      }
      // The label space belongs to the evaluator; the user only supplies guidance on how to
      // assign labels, and only when labels are opted in. Omit it entirely when they are off.
      if (includeLabels) {
        payload.label_guidance = labelGuidance;
      }
      // Omitted unless prescribed, which leaves the distribution to the instructions.
      if (mixActive) {
        payload.label_mix = toProportions(mixPercents);
      }
      const created = await datasets.generateFromVersion(payload);
      navigate(`/datasets/${created.dataset.id}`);
    } catch (err) {
      // Keep the form filled in so the user can correct and retry after a ContractError.
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={open}
      title="Generate dataset"
      description="The column shape is derived from the evaluator; your instructions only steer the content generated into it."
      onClose={onClose}
      footer={
        <div className="modal-actions">
          <Button variant="secondary" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={submitting || !canSubmit}>
            {submitting ? <Spinner /> : "Generate"}
          </Button>
        </div>
      }
    >
      <div className="generate-form">
        <ErrorBanner error={error} onDismiss={() => setError(null)} />

        <label className="field">
          <span className="field-label">Dataset name</span>
          <input className="select" value={name} onChange={(e) => setName(e.target.value)} />
        </label>

        <div className="field">
          <span className="field-label">
            Columns
            <Tooltip text="Extra columns must be named explicitly here — the model never invents columns you did not ask for." />
          </span>
          <ColumnNotesEditor
            lockedColumns={version.required_columns}
            extraColumns={extraColumns}
            notes={notes}
            notePlaceholder="What should this column contain?"
            lockedBadge="required"
            allowAddColumns
            onChangeNotes={setNotes}
            onChangeExtraColumns={setExtraColumns}
          />
        </div>

        <label className="field">
          <span className="field-label">Instructions</span>
          <textarea
            className="textarea"
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
          />
        </label>

        <label className="field-inline">
          <input
            type="checkbox"
            checked={includeLabels}
            onChange={(e) => setIncludeLabels(e.target.checked)}
          />
          Include suggested labels
        </label>

        {includeLabels && (
          <>
            <div className="field">
              <span className="field-label">Label space</span>
              <div className="label-space">
                {version.score_kind === "categorical" ? (
                  (version.score_labels ?? []).map((label) => (
                    <span key={label} className="badge">
                      {label}
                    </span>
                  ))
                ) : (
                  <span className="badge">
                    {version.score_minimum} – {version.score_maximum}
                  </span>
                )}
              </div>
            </div>

            <label className="field">
              <span className="field-label">Label guidance</span>
              <textarea
                className="textarea"
                value={labelGuidance}
                onChange={(e) => setLabelGuidance(e.target.value)}
              />
            </label>

            <LabelMixEditor
              labels={mixLabels}
              percents={mixPercents}
              enabled={mixEnabled}
              count={count}
              onChangeEnabled={setMixEnabled}
              onChangePercents={setMixPercents}
            />
          </>
        )}

        <label className="field">
          <span className="field-label">Row count</span>
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
          <p className="destructive-warning">Row count must be {maxCount} or fewer.</p>
        )}
      </div>
    </Modal>
  );
}
