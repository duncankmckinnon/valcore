// Side-by-side comparison of two runs over the same dataset. The API enforces the
// same-dataset rule and returns a 422 otherwise, which we surface as an error banner.
// Rows come back disagreements-first from the API.

import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { runs } from "../api/client";
import type { CompareOut, Run } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { CompareIcon } from "../components/icons";
import { PageHeader } from "../components/PageHeader";
import { Badge, ErrorBanner, Select, Spinner, Table } from "../components/ui";

function runLabel(run: Run): string {
  return `${run.id.slice(0, 8)} · ${run.kind}`;
}

function DeltaBadge({ metric, value }: { metric: string; value: number }) {
  // Lower is better for error metrics; higher is better for everything else.
  const lowerIsBetter = metric === "mae" || metric === "rmse";
  const improved = value === 0 ? null : lowerIsBetter ? value < 0 : value > 0;
  const tone = improved === null ? "neutral" : improved ? "success" : "danger";
  const arrow = value === 0 ? "→" : value > 0 ? "▲" : "▼";
  return (
    <div className="metric">
      <span className="metric-label">{metric}</span>
      <Badge tone={tone}>
        {arrow} {value > 0 ? "+" : ""}
        {value.toFixed(3)}
      </Badge>
    </div>
  );
}

export default function ComparePage() {
  const [params, setParams] = useSearchParams();
  const [runList, setRunList] = useState<Run[]>([]);
  const [comparison, setComparison] = useState<CompareOut | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  const a = params.get("a") ?? "";
  const b = params.get("b") ?? "";

  useEffect(() => {
    runs.list().then(setRunList).catch(setError);
  }, []);

  useEffect(() => {
    if (!a || !b) {
      setComparison(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    runs
      .compare(a, b)
      .then((result) => !cancelled && setComparison(result))
      .catch((err) => {
        if (cancelled) return;
        setComparison(null);
        setError(err);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [a, b]);

  function pick(side: "a" | "b", value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(side, value);
    else next.delete(side);
    setParams(next);
  }

  const options = [
    { value: "", label: "Select a run…" },
    ...runList.map((run) => ({ value: run.id, label: runLabel(run) })),
  ];

  return (
    <section>
      <div className="detail-breadcrumb">
        <Link to="/runs">Runs</Link> / Compare
      </div>
      <PageHeader
        title="Compare"
        description="See where two runs disagree, row by row."
      />

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <div className="compare-selectors">
        <label className="field">
          <span className="field-label">Run A</span>
          <Select aria-label="Run A" value={a} options={options} onChange={(e) => pick("a", e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Run B</span>
          <Select aria-label="Run B" value={b} options={options} onChange={(e) => pick("b", e.target.value)} />
        </label>
      </div>

      {loading && <Spinner />}

      {!loading && (!a || !b) && (
        <EmptyState
          icon={<CompareIcon />}
          message="Pick two runs above to see where they disagree, row by row."
        />
      )}

      {comparison && (
        <>
          <div className="metrics-summary compare-deltas">
            {Object.keys(comparison.metrics_delta).length === 0 ? (
              <p className="muted">No shared numeric metrics to compare.</p>
            ) : (
              Object.entries(comparison.metrics_delta).map(([metric, value]) => (
                <DeltaBadge key={metric} metric={metric} value={value} />
              ))
            )}
          </div>

          <Table
            rows={comparison.rows}
            rowKey={(row) => row.row_id}
            empty="No rows to compare."
            columns={[
              { header: "#", cell: (row) => row.idx },
              {
                header: "Row",
                cell: (row) => (
                  <span className="reasoning">
                    {Object.entries(row.data)
                      .map(([k, v]) => `${k}: ${String(v)}`)
                      .join(" · ")}
                  </span>
                ),
              },
              {
                header: "A output",
                cell: (row) => <OutputCell output={row.output_a} score={row.score_a} />,
              },
              {
                header: "B output",
                cell: (row) => <OutputCell output={row.output_b} score={row.score_b} />,
              },
              { header: "Label", cell: (row) => (row.label ?? "—") },
              {
                header: "",
                cell: (row) =>
                  row.disagree ? <Badge tone="warning">differs</Badge> : <span className="muted">—</span>,
              },
            ]}
          />
        </>
      )}
    </section>
  );
}

function OutputCell({
  output,
  score,
}: {
  output: Record<string, unknown> | null;
  score: string | number | null;
}) {
  return (
    <div className="compare-output">
      <strong>{score ?? "—"}</strong>
      {output && (
        <span className="reasoning">
          {Object.entries(output)
            .map(([k, v]) => `${k}: ${String(v)}`)
            .join(" · ")}
        </span>
      )}
    </div>
  );
}
