// A self-contained scratchpad for trying an agent version before deciding whether its
// response belongs in a persisted dataset derivation. Keeping this separate from the
// detail page makes the ephemeral-versus-saved boundary explicit and reusable.

import { useEffect, useMemo, useState } from "react";
import { agents, datasets } from "../api/client";
import type { AgentVersion, DatasetSummary, TrialResult } from "../api/types";
import { Button, ConfirmDialog, ErrorBanner, Select, Spinner } from "./ui";

/** Props for a scratch trial bound to one immutable agent version. */
export type AgentTrialPanelProps = {
  version: AgentVersion;
};

type SavedTrial = {
  inputs: Record<string, unknown>;
  result: TrialResult;
};

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

/** Runs a version with ad-hoc inputs and optionally persists its one response. */
export default function AgentTrialPanel({ version }: AgentTrialPanelProps) {
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [trial, setTrial] = useState<SavedTrial | null>(null);
  const [unsaved, setUnsaved] = useState(false);
  const [rerunConfirmOpen, setRerunConfirmOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [datasetList, setDatasetList] = useState<DatasetSummary[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [savedOrdinal, setSavedOrdinal] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    datasets
      .list()
      .then((listed) => {
        if (!cancelled) setDatasetList(listed);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!unsaved) return;
    const warnBeforeUnload = (event: BeforeUnloadEvent): string => {
      event.preventDefault();
      event.returnValue = "";
      return "";
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [unsaved]);

  const canSave = useMemo(
    () =>
      trial !== null &&
      trial.result.error === null &&
      datasetId !== "" &&
      !saving,
    [datasetId, saving, trial],
  );

  async function runTrial(): Promise<void> {
    const trialInputs: Record<string, unknown> = { ...inputs };
    setRunning(true);
    setError(null);
    setSavedOrdinal(null);
    try {
      const result = await agents.trial(version.id, { inputs: trialInputs });
      setTrial({ inputs: trialInputs, result });
      setUnsaved(true);
    } catch (err) {
      // Retaining the previous result lets a transient API failure be retried or saved.
      setError(err);
    } finally {
      setRunning(false);
    }
  }

  function requestRun(): void {
    if (unsaved) {
      setRerunConfirmOpen(true);
      return;
    }
    void runTrial();
  }

  async function saveTrial(): Promise<void> {
    if (!trial || !datasetId || trial.result.error !== null) return;
    setSaving(true);
    setError(null);
    try {
      const derivation = await agents.saveDerivation(version.id, {
        dataset_id: datasetId,
        entries: [{ inputs: trial.inputs, data: trial.result.output }],
      });
      setUnsaved(false);
      setSavedOrdinal(derivation.ordinal);
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  }

  function discard(): void {
    setTrial(null);
    setUnsaved(false);
    setSavedOrdinal(null);
    setError(null);
  }

  return (
    <section aria-label="Agent trial">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {version.required_columns.map((column) => (
        <label className="field" key={column}>
          <span className="field-label">{column}</span>
          <input
            value={inputs[column] ?? ""}
            onChange={(event) =>
              setInputs((current) => ({
                ...current,
                [column]: event.target.value,
              }))
            }
            aria-label={column}
          />
        </label>
      ))}

      <div className="form-actions">
        <Button onClick={requestRun} disabled={running}>
          Run
        </Button>
        {running && <Spinner />}
      </div>

      {trial && (
        <div>
          <h3>Trial result</h3>
          <div className="field">
            <span className="field-label">Prompt</span>
            <p>{trial.result.prompt}</p>
          </div>
          {trial.result.error ? (
            <div className="error-banner" role="alert">
              {trial.result.error}
            </div>
          ) : (
            trial.result.response_columns.map((column) => (
              <label className="field" key={column}>
                <span className="field-label">{column}</span>
                <textarea
                  aria-label={column}
                  readOnly
                  value={displayValue(trial.result.output[column])}
                />
              </label>
            ))
          )}
          <p>Latency: {trial.result.latency_ms} ms</p>

          <label className="field">
            <span className="field-label">Dataset</span>
            <Select
              aria-label="Dataset"
              value={datasetId}
              options={[
                { value: "", label: "Select a dataset…" },
                ...datasetList.map((dataset) => ({
                  value: dataset.id,
                  label: dataset.name,
                })),
              ]}
              onChange={(event) => setDatasetId(event.target.value)}
            />
          </label>
          <div className="form-actions">
            <Button onClick={() => void saveTrial()} disabled={!canSave}>
              Save
            </Button>
            <Button variant="secondary" onClick={discard} disabled={saving}>
              Discard
            </Button>
          </div>
          {savedOrdinal !== null && <p>Saved as derivation {savedOrdinal}.</p>}
        </div>
      )}

      <ConfirmDialog
        open={rerunConfirmOpen}
        title="Discard unsaved response?"
        message="Running again will discard the unsaved response."
        confirmLabel="Run again"
        onClose={() => setRerunConfirmOpen(false)}
        onConfirm={() => {
          setRerunConfirmOpen(false);
          void runTrial();
        }}
      />
    </section>
  );
}
