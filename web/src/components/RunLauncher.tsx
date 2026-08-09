// Start a run: pick an evaluator, one of its versions, and a dataset, choose the run
// kind and concurrency, then Start. Validation requires a fully labeled dataset, so
// that option is disabled (with an explanation) when the chosen dataset has unlabeled
// rows.

import { useEffect, useMemo, useState } from "react";
import { datasets, evaluators, runs } from "../api/client";
import type { Dataset, DatasetStats, Evaluator, EvaluatorVersion, Run, RunKind } from "../api/types";
import { Button, ErrorBanner, Select, Spinner } from "./ui";
import { GATEWAY_BLOCKER, useSetup } from "./useSetup";

type EvaluatorWithVersions = Evaluator & { versions: EvaluatorVersion[] };

type Props = {
  onStarted: (run: Run) => void;
};

export default function RunLauncher({ onStarted }: Props) {
  const [evaluatorList, setEvaluatorList] = useState<Evaluator[]>([]);
  const [datasetList, setDatasetList] = useState<Dataset[]>([]);
  const [versions, setVersions] = useState<EvaluatorVersion[]>([]);
  const [stats, setStats] = useState<DatasetStats | null>(null);

  const [evaluatorId, setEvaluatorId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [kind, setKind] = useState<RunKind>("eval");
  const [concurrency, setConcurrency] = useState(8);

  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const { gatewayReady } = useSetup();

  useEffect(() => {
    evaluators.list().then(setEvaluatorList).catch(setError);
    datasets.list().then(setDatasetList).catch(setError);
  }, []);

  useEffect(() => {
    if (!evaluatorId) {
      setVersions([]);
      setVersionId("");
      return;
    }
    let cancelled = false;
    (evaluators.get(evaluatorId) as unknown as Promise<EvaluatorWithVersions>)
      .then((data) => {
        if (cancelled) return;
        setVersions(data.versions);
        setVersionId(data.active_version_id ?? data.versions[0]?.id ?? "");
      })
      .catch((err) => !cancelled && setError(err));
    return () => {
      cancelled = true;
    };
  }, [evaluatorId]);

  useEffect(() => {
    if (!datasetId) {
      setStats(null);
      return;
    }
    let cancelled = false;
    datasets
      .stats(datasetId)
      .then((s) => !cancelled && setStats(s))
      .catch(() => !cancelled && setStats(null));
    return () => {
      cancelled = true;
    };
  }, [datasetId]);

  const hasUnlabeled = stats !== null && stats.unlabeled > 0;
  const validationDisabled = hasUnlabeled;

  // A dataset with unlabeled rows cannot be validated; fall back to a plain eval run.
  useEffect(() => {
    if (validationDisabled && kind === "validation") setKind("eval");
  }, [validationDisabled, kind]);

  const canStart = useMemo(
    () => versionId !== "" && datasetId !== "" && concurrency > 0 && !submitting && gatewayReady,
    [versionId, datasetId, concurrency, submitting, gatewayReady],
  );

  async function start() {
    setSubmitting(true);
    setError(null);
    try {
      const run = await runs.create({
        kind,
        version_id: versionId,
        dataset_id: datasetId,
        concurrency,
      });
      onStarted(run);
    } catch (err) {
      setError(err);
      setSubmitting(false);
    }
  }

  return (
    <div className="run-launcher">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <label className="field">
        <span className="field-label">Evaluator</span>
        <Select
          aria-label="Evaluator"
          value={evaluatorId}
          options={[
            { value: "", label: "Select an evaluator…" },
            ...evaluatorList.map((e) => ({ value: e.id, label: e.name })),
          ]}
          onChange={(e) => setEvaluatorId(e.target.value)}
        />
      </label>

      <label className="field">
        <span className="field-label">Version</span>
        <Select
          aria-label="Version"
          value={versionId}
          disabled={versions.length === 0}
          options={
            versions.length === 0
              ? [{ value: "", label: "—" }]
              : versions.map((v) => ({
                  value: v.id,
                  label: `${v.version_name}${v.frozen ? " (frozen)" : ""}`,
                }))
          }
          onChange={(e) => setVersionId(e.target.value)}
        />
      </label>

      <label className="field">
        <span className="field-label">Dataset</span>
        <Select
          aria-label="Dataset"
          value={datasetId}
          options={[
            { value: "", label: "Select a dataset…" },
            ...datasetList.map((d) => ({ value: d.id, label: d.name })),
          ]}
          onChange={(e) => setDatasetId(e.target.value)}
        />
      </label>

      <label className="field">
        <span className="field-label">Run kind</span>
        <Select
          aria-label="Run kind"
          value={kind}
          options={[
            { value: "eval", label: "Eval — score every row" },
            {
              value: "validation",
              label: validationDisabled
                ? "Validation (needs a fully labeled dataset)"
                : "Validation — measure agreement with labels",
            },
          ]}
          onChange={(e) => setKind(e.target.value as RunKind)}
        />
        {validationDisabled && (
          <span className="muted">
            Validation is unavailable: {stats?.unlabeled} row(s) in this dataset are unlabeled.
          </span>
        )}
      </label>

      <label className="field">
        <span className="field-label">Concurrency</span>
        <input
          className="select"
          type="number"
          min={1}
          max={64}
          aria-label="Concurrency"
          value={concurrency}
          onChange={(e) => setConcurrency(Number(e.target.value))}
        />
      </label>

      <div className="form-actions">
        {!gatewayReady && <span className="form-footer-blocker">{GATEWAY_BLOCKER}</span>}
        <Button onClick={start} disabled={!canStart}>
          {submitting ? <Spinner /> : "Start"}
        </Button>
      </div>
    </div>
  );
}
