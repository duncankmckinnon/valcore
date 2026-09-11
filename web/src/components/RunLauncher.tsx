// Start a run: pick an evaluator, one of its versions, and a dataset, choose the run
// kind and concurrency, then Start. Validation requires a matching label set, so that
// option is disabled (with an explanation) when no label set on the dataset matches
// the chosen evaluator version's score space; partial coverage only warns.

import { useEffect, useMemo, useState } from "react";
import { datasets, evaluators, runs } from "../api/client";
import type { DatasetSummary, Evaluator, EvaluatorVersion, Run, RunCoverage, RunKind } from "../api/types";
import { Button, ErrorBanner, Select, Spinner } from "./ui";
import { GATEWAY_BLOCKER, useSetup } from "./useSetup";

type EvaluatorWithVersions = Evaluator & { versions: EvaluatorVersion[] };

type Props = {
  onStarted: (run: Run) => void;
};

export default function RunLauncher({ onStarted }: Props) {
  const [evaluatorList, setEvaluatorList] = useState<Evaluator[]>([]);
  const [datasetList, setDatasetList] = useState<DatasetSummary[]>([]);
  const [versions, setVersions] = useState<EvaluatorVersion[]>([]);
  const [coverage, setCoverage] = useState<RunCoverage | null>(null);

  const [evaluatorId, setEvaluatorId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [kind, setKind] = useState<RunKind>("eval");
  // Off by default: the runner engine keeps cancellation and per-row retry, so an experiment
  // is the deliberate choice you make when you want it in Logfire's experiments view.
  const [experiment, setExperiment] = useState(false);
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
    if (!datasetId || !versionId) {
      setCoverage(null);
      return;
    }
    let cancelled = false;
    runs
      .coverage(datasetId, versionId)
      .then((c) => !cancelled && setCoverage(c))
      .catch(() => !cancelled && setCoverage(null));
    return () => {
      cancelled = true;
    };
  }, [datasetId, versionId]);

  const noCoverage = coverage !== null && coverage.label_set_id === null;
  const partialCoverage =
    coverage !== null && coverage.label_set_id !== null && coverage.labeled_rows < coverage.total_rows;
  const validationDisabled = noCoverage;

  // A dataset/version pairing with no matching label set cannot be validated; fall back
  // to a plain eval run. Partial coverage no longer disables validation -- it now runs on
  // whichever rows have a valid label, which is exactly what the warning below explains.
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
        experiment,
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
                ? "Validation (no label set matches this evaluator)"
                : "Validation — measure agreement with labels",
            },
          ]}
          onChange={(e) => setKind(e.target.value as RunKind)}
        />
        {validationDisabled && (
          <span className="muted">
            Validation is unavailable: no label set on this dataset matches this evaluator
            version&apos;s score space. Only Eval can run.
          </span>
        )}
        {!validationDisabled && partialCoverage && kind === "validation" && (
          <span className="muted">
            {coverage?.labeled_rows} of {coverage?.total_rows} rows have a label for this
            evaluator; validation will run on {coverage?.labeled_rows} row
            {coverage?.labeled_rows === 1 ? "" : "s"}.
          </span>
        )}
      </label>

      {/* An engine choice, not a run kind: an experiment can be eval or validation, which is
          why this is a checkbox beside the kind select rather than a third option inside it. */}
      <label className="field-inline">
        <input
          type="checkbox"
          checked={experiment}
          onChange={(event) => setExperiment(event.target.checked)}
        />
        Run as a Logfire experiment
      </label>
      {experiment && (
        <span className="muted">
          Runs through pydantic-evals so it appears in Logfire&apos;s experiments view on
          the valcore project. Cannot be cancelled once started, and re-running single rows
          is unavailable.
        </span>
      )}

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
