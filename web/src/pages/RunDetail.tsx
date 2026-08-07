// Detail view for a single run. While the run is in-flight it shows RunProgress;
// once terminal it shows the metrics panel and a filterable results table.

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { datasets, runs } from "../api/client";
import type { ResultRow, Run, RunStatus } from "../api/types";
import MetricsPanel from "../components/MetricsPanel";
import { PageHeader } from "../components/PageHeader";
import RunProgress from "../components/RunProgress";
import { Badge, Button, ErrorBanner, Spinner, Table } from "../components/ui";

type Props = {
  runId: string;
};

const TERMINAL: ReadonlySet<RunStatus> = new Set<RunStatus>([
  "completed",
  "completed_with_errors",
  "cancelled",
  "failed",
]);

function renderData(data: Record<string, unknown>): string {
  return Object.entries(data)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(" · ");
}

function renderOutput(output: Record<string, unknown> | null): string {
  if (!output) return "—";
  return Object.entries(output)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(" · ");
}

function AgreementCell({ agreement }: { agreement: boolean | number | null }) {
  if (agreement === null) return <span className="muted">—</span>;
  if (typeof agreement === "boolean") {
    return agreement ? (
      <Badge tone="success">✓ agree</Badge>
    ) : (
      <Badge tone="danger">✗ disagree</Badge>
    );
  }
  return agreement === 0 ? (
    <Badge tone="success">Δ 0</Badge>
  ) : (
    <Badge tone="warning">Δ {agreement > 0 ? "+" : ""}{agreement}</Badge>
  );
}

export default function RunDetail({ runId }: Props) {
  const [run, setRun] = useState<Run | null>(null);
  const [total, setTotal] = useState(0);
  const [results, setResults] = useState<ResultRow[]>([]);
  const [onlyDisagreements, setOnlyDisagreements] = useState(false);
  const [onlyErrors, setOnlyErrors] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [retrying, setRetrying] = useState(false);

  const loadRun = useCallback(() => {
    runs
      .get(runId)
      .then(setRun)
      .catch(setError);
  }, [runId]);

  useEffect(() => {
    loadRun();
  }, [loadRun]);

  // The progress bar needs the row count; the SSE replay only carries the completed
  // count, so fetch the dataset's total once the run (and its dataset id) is known.
  useEffect(() => {
    if (!run) return;
    datasets
      .stats(run.dataset_id)
      .then((s) => setTotal(s.total))
      .catch(() => {
        // A missing count leaves the bar at an unknown total; non-critical.
      });
  }, [run]);

  const isTerminal = run !== null && TERMINAL.has(run.status);

  const loadResults = useCallback(() => {
    runs
      .results(runId, { only_disagreements: onlyDisagreements, only_errors: onlyErrors, limit: 500 })
      .then((page) => setResults(page.results))
      .catch(setError);
  }, [runId, onlyDisagreements, onlyErrors]);

  useEffect(() => {
    if (isTerminal) loadResults();
  }, [isTerminal, loadResults]);

  async function retry() {
    setRetrying(true);
    setError(null);
    try {
      const updated = await runs.retryFailed(runId);
      setRun(updated);
    } catch (err) {
      setError(err);
    } finally {
      setRetrying(false);
    }
  }

  if (!run) {
    return error ? <ErrorBanner error={error} /> : <Spinner />;
  }

  return (
    <section>
      <div className="detail-breadcrumb">
        <Link to="/runs">Runs</Link> / {run.kind} run
      </div>
      <PageHeader title={`Run ${run.id.slice(0, 8)}`} />

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {!isTerminal ? (
        <RunProgress
          runId={runId}
          total={total}
          startedAt={run.started_at}
          onFinished={() => loadRun()}
        />
      ) : (
        <>
          <MetricsPanel metrics={run.metrics} />

          <div className="results-toolbar">
            <label className="toggle">
              <input
                type="checkbox"
                checked={onlyDisagreements}
                onChange={(e) => setOnlyDisagreements(e.target.checked)}
              />
              Disagreements only
            </label>
            <label className="toggle">
              <input
                type="checkbox"
                checked={onlyErrors}
                onChange={(e) => setOnlyErrors(e.target.checked)}
              />
              Errors only
            </label>
            {run.status === "completed_with_errors" && (
              <Button variant="secondary" onClick={retry} disabled={retrying}>
                {retrying ? <Spinner /> : "Retry failed rows"}
              </Button>
            )}
          </div>

          <Table<ResultRow>
            rows={results}
            rowKey={(r) => r.result_id}
            empty="No results match the current filters."
            columns={[
              { header: "#", cell: (r) => r.idx },
              { header: "Row", cell: (r) => <span className="reasoning">{renderData(r.data)}</span> },
              {
                header: "Output",
                cell: (r) =>
                  r.error ? (
                    <span className="tick-error">{r.error}</span>
                  ) : (
                    <span className="reasoning">{renderOutput(r.output)}</span>
                  ),
              },
              { header: "Score", cell: (r) => (r.score_value ?? "—") },
              { header: "Label", cell: (r) => (r.label ?? "—") },
              { header: "Agreement", cell: (r) => <AgreementCell agreement={r.agreement} /> },
            ]}
          />
        </>
      )}
    </section>
  );
}
