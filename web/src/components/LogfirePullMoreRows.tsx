// Top-up flow: pull more rows from Logfire into a dataset that already carries a SQL-pull
// provenance. Every field is prefilled from the dataset's stored pull settings so repeating the
// same ask needs only a click — seed is the one exception, left blank so a fresh sample is drawn
// by default instead of resampling the same rows from an unwidened time window.

import { useEffect, useState } from "react";
import { datasets } from "../api/client";
import type { Dataset, DatasetLogfirePull, RowsLogfirePull } from "../api/types";
import { Button, ErrorBanner, Modal, Spinner } from "./ui";
import { FormFooter } from "./FormFooter";
import { useSetup } from "./useSetup";

type LogfirePullMoreRowsProps = {
  open: boolean;
  dataset: Dataset;
  pull: DatasetLogfirePull;
  onPulled: (added: number) => void;
  onClose: () => void;
};

const SET_KEY = "Set the Logfire read key on the Settings page to pull.";

export default function LogfirePullMoreRows({
  open,
  dataset,
  pull,
  onPulled,
  onClose,
}: LogfirePullMoreRowsProps) {
  const { status } = useSetup();
  const [sql, setSql] = useState("");
  const [sampleN, setSampleN] = useState(1);
  const [seed, setSeed] = useState("");
  const [minTimestamp, setMinTimestamp] = useState("");
  const [maxTimestamp, setMaxTimestamp] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const logfireKeySet = status?.keys.some((key) => key.name === "logfire_read_key" && key.set);

  // Prefill when the modal opens rather than on mount: `pull` is fetched by the parent and
  // may change identity after this component first renders.
  useEffect(() => {
    if (!open) return;
    setSql(pull.sql);
    setSampleN(pull.sample_n);
    setSeed("");
    setMinTimestamp(pull.min_timestamp ?? "");
    setMaxTimestamp(pull.max_timestamp ?? "");
    setError(null);
  }, [open, pull]);

  const blockers: string[] = [];
  if (logfireKeySet === false) blockers.push(SET_KEY);
  if (sql.trim() === "") blockers.push("SQL must not be empty.");
  if (sampleN < 1) blockers.push("Sample at least one top-level entry.");

  const canSubmit = blockers.length === 0;

  async function submit() {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const seedValue = seed.trim() === "" ? undefined : Number(seed);
      const payload: RowsLogfirePull = {
        sql: sql.trim(),
        sample_n: sampleN,
        ...(seedValue !== undefined && !Number.isNaN(seedValue) ? { seed: seedValue } : {}),
        ...(minTimestamp.trim() !== "" ? { min_timestamp: minTimestamp.trim() } : {}),
        ...(maxTimestamp.trim() !== "" ? { max_timestamp: maxTimestamp.trim() } : {}),
      };
      const added = await datasets.pullMoreFromLogfire(dataset.id, payload);
      onPulled(added.length);
    } catch (err) {
      // Keep the form filled in so the user can correct and retry.
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={open}
      title="Pull more from Logfire"
      description="New rows append to this dataset and reuse its stored pull settings."
      onClose={onClose}
      footer={
        <FormFooter blockers={blockers}>
          <Button variant="secondary" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={submitting || !canSubmit}>
            {submitting ? <Spinner /> : "Pull"}
          </Button>
        </FormFooter>
      }
    >
      <div className="generate-form">
        <ErrorBanner error={error} onDismiss={() => setError(null)} />

        <p className="field-hint">
          New rows must come back with this dataset&apos;s existing columns, so they stay
          compatible with the rows already here.
        </p>

        <label className="field">
          <span className="field-label">SQL</span>
          <textarea
            className="textarea"
            aria-label="SQL"
            value={sql}
            onChange={(e) => setSql(e.target.value)}
            rows={6}
            spellCheck={false}
          />
        </label>

        <label className="field">
          <span className="field-label">Sample size</span>
          <input
            className="input"
            type="number"
            min={1}
            value={sampleN}
            onChange={(e) => setSampleN(Number(e.target.value))}
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
      </div>
    </Modal>
  );
}
