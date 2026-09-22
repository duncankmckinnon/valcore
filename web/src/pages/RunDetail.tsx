// Detail view for a single run. While the run is in-flight it shows RunProgress;
// once terminal it shows the metrics panel and a filterable results table.

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { agents, datasets, runs } from "../api/client";
import type {
  Derivation,
  DerivedRowsPage,
  ResultRow,
  Run,
  RunStatus,
} from "../api/types";
import MetricsPanel from "../components/MetricsPanel";
import { PageHeader } from "../components/PageHeader";
import RunProgress from "../components/RunProgress";
import {
  Badge,
  Button,
  ConfirmDialog,
  ErrorBanner,
  Spinner,
  Table,
} from "../components/ui";

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
    <Badge tone="warning">
      Δ {agreement > 0 ? "+" : ""}
      {agreement}
    </Badge>
  );
}

/** Makes machine-readable skip reasons understandable beside partial-run metrics. */
function SkippedRows({ metrics }: { metrics: Run["metrics"] }) {
  const skipped = metrics?.skipped;
  if (!skipped || Object.keys(skipped).length === 0) return null;
  return (
    <p className="field-hint">
      Skipped rows:{" "}
      {Object.entries(skipped)
        .map(([reason, count]) => `${reason.replace(/_/g, " ")}: ${count}`)
        .join(", ")}
    </p>
  );
}

/** Displays the original dataset columns alongside the response overlay from an agent pass. */
function DerivationRows({ page }: { page: DerivedRowsPage }) {
  return (
    <Table
      rows={page.rows}
      rowKey={(row) => row.row_id}
      empty="No responses were produced."
      columns={[
        { header: "#", cell: (row) => row.idx },
        ...page.columns.map((column) => ({
          header: column,
          cell: (row: DerivedRowsPage["rows"][number]) =>
            row.data[column] === undefined ? "—" : String(row.data[column]),
        })),
        {
          header: "Error",
          cell: (row) => row.error ?? "—",
        },
        {
          header: "Latency",
          cell: (row) =>
            row.latency_ms === null ? "—" : `${row.latency_ms} ms`,
        },
      ]}
    />
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
  const [derivation, setDerivation] = useState<Derivation | null>(null);
  const [derivedRows, setDerivedRows] = useState<DerivedRowsPage | null>(null);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [savingDerivation, setSavingDerivation] = useState(false);
  const [discardingDerivation, setDiscardingDerivation] = useState(false);

  const loadRun = useCallback(() => {
    runs.get(runId).then(setRun).catch(setError);
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
      .results(runId, {
        only_disagreements: onlyDisagreements,
        only_errors: onlyErrors,
        limit: 500,
      })
      .then((page) => setResults(page.results))
      .catch(setError);
  }, [runId, onlyDisagreements, onlyErrors]);

  useEffect(() => {
    if (isTerminal && run?.kind !== "derive") loadResults();
  }, [isTerminal, loadResults, run?.kind]);

  useEffect(() => {
    if (!run || run.kind !== "derive" || !run.derivation_id) return;
    let cancelled = false;
    agents
      .listDerivations({ datasetId: run.dataset_id, includeStaged: true })
      .then((items) => {
        const found =
          items.find((item) => item.id === run.derivation_id) ?? null;
        if (!cancelled) setDerivation(found);
      })
      .catch(setError);
    return () => {
      cancelled = true;
    };
  }, [run]);

  useEffect(() => {
    if (!derivation || !isTerminal) return;
    agents.derivedRows(derivation.id).then(setDerivedRows).catch(setError);
  }, [derivation, isTerminal]);

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

  async function saveDerivation(): Promise<void> {
    if (!derivation) return;
    setSavingDerivation(true);
    setError(null);
    try {
      setDerivation(await agents.acceptDerivation(derivation.id));
    } catch (err) {
      setError(err);
    } finally {
      setSavingDerivation(false);
    }
  }

  async function discardDerivation(): Promise<void> {
    if (!derivation) return;
    setDiscardingDerivation(true);
    setError(null);
    try {
      await agents.deleteDerivation(derivation.id);
      setDerivation(null);
      setDerivedRows(null);
      setDiscardOpen(false);
    } catch (err) {
      setError(err);
    } finally {
      setDiscardingDerivation(false);
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
          <SkippedRows metrics={run.metrics} />

          {run.kind === "derive" && derivation && (
            <section>
              <h2>Derivation</h2>
              <p>
                {derivation.agent_name} / {derivation.version_name}
                {derivation.state === "saved"
                  ? ` / derivation ${derivation.ordinal}`
                  : " / staged"}
              </p>
              {derivation.state === "staged" && (
                <div className="form-actions">
                  <Button
                    onClick={() => void saveDerivation()}
                    disabled={savingDerivation || discardingDerivation}
                  >
                    {savingDerivation ? <Spinner /> : "Save"}
                  </Button>
                  <Button
                    variant="danger"
                    onClick={() => setDiscardOpen(true)}
                    disabled={savingDerivation || discardingDerivation}
                  >
                    Discard
                  </Button>
                </div>
              )}
              {derivedRows ? (
                <DerivationRows page={derivedRows} />
              ) : (
                <Spinner />
              )}
            </section>
          )}

          {run.kind !== "derive" && (
            <>
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
                  <Button
                    variant="secondary"
                    onClick={retry}
                    disabled={retrying}
                  >
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
                  {
                    header: "Row",
                    cell: (r) => (
                      <span className="reasoning">{renderData(r.data)}</span>
                    ),
                  },
                  {
                    header: "Output",
                    cell: (r) =>
                      r.error ? (
                        <span className="tick-error">{r.error}</span>
                      ) : (
                        <span className="reasoning">
                          {renderOutput(r.output)}
                        </span>
                      ),
                  },
                  { header: "Score", cell: (r) => r.score_value ?? "—" },
                  { header: "Label", cell: (r) => r.label ?? "—" },
                  {
                    header: "Agreement",
                    cell: (r) => <AgreementCell agreement={r.agreement} />,
                  },
                ]}
              />
            </>
          )}
        </>
      )}
      <ConfirmDialog
        open={discardOpen}
        title="Discard derivation"
        message="Discard these staged responses? This cannot be undone."
        confirmLabel="Discard"
        busy={discardingDerivation}
        onConfirm={() => void discardDerivation()}
        onClose={() => setDiscardOpen(false)}
      />
    </section>
  );
}
