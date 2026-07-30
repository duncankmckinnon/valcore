// Renders a run's agreement metrics. The kind is inferred from the shape emitted by
// `evalcore.metrics`: categorical metrics carry a `confusion` matrix, numeric ones
// carry `mae`/`rmse`. No charting library — the confusion matrix is a shaded table.

import type { ReactNode } from "react";

type Props = {
  metrics: Record<string, unknown> | null;
};

type PerLabel = { precision: number; recall: number; f1: number; support: number };

const CORRELATION_NOTE = "Correlation is n/a when either series has zero variance.";

function pct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function fixed(value: number): string {
  return value.toFixed(3);
}

function correlation(value: unknown): string {
  return value === null || value === undefined ? "n/a" : fixed(value as number);
}

function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="metric">
      <span className="metric-label">{label}</span>
      <span className="metric-value">{value}</span>
    </div>
  );
}

export default function MetricsPanel({ metrics }: Props) {
  if (!metrics) {
    return <p className="muted">No metrics for this run.</p>;
  }
  if ("confusion" in metrics) {
    return <CategoricalPanel metrics={metrics} />;
  }
  if ("mae" in metrics) {
    return <NumericPanel metrics={metrics} />;
  }
  return <p className="muted">No metrics for this run.</p>;
}

function CategoricalPanel({ metrics }: { metrics: Record<string, unknown> }) {
  const accuracy = metrics.accuracy as number;
  const kappa = metrics.cohens_kappa as number;
  const n = metrics.n as number;
  const perLabel = metrics.per_label as Record<string, PerLabel>;
  const confusion = metrics.confusion as Record<string, Record<string, number>>;
  const labels = Object.keys(confusion);
  const maxCount = Math.max(1, ...labels.flatMap((a) => labels.map((p) => confusion[a][p])));

  return (
    <div className="metrics-panel">
      <div className="metrics-summary">
        <Metric label="Accuracy" value={pct(accuracy)} />
        <Metric label="Cohen's κ" value={fixed(kappa)} />
        <Metric label="n" value={n} />
      </div>

      <h3>Per-label</h3>
      <table className="table metrics-per-label">
        <thead>
          <tr>
            <th>Label</th>
            <th>Precision</th>
            <th>Recall</th>
            <th>F1</th>
            <th>Support</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(perLabel).map(([label, m]) => (
            <tr key={label} data-label={label}>
              <td>{label}</td>
              <td>{fixed(m.precision)}</td>
              <td>{fixed(m.recall)}</td>
              <td>{fixed(m.f1)}</td>
              <td>{m.support}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Confusion matrix</h3>
      <p className="muted">Rows are the human label; columns are the evaluator's score.</p>
      <table className="table confusion-matrix">
        <thead>
          <tr>
            <th />
            {labels.map((p) => (
              <th key={p}>{p}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {labels.map((actual) => (
            <tr key={actual}>
              <th>{actual}</th>
              {labels.map((predicted) => {
                const count = confusion[actual][predicted];
                const alpha = count === 0 ? 0 : 0.15 + 0.85 * (count / maxCount);
                return (
                  <td
                    key={predicted}
                    className="confusion-cell"
                    data-actual={actual}
                    data-predicted={predicted}
                    style={{ backgroundColor: `rgba(47, 111, 235, ${alpha})` }}
                  >
                    {count}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NumericPanel({ metrics }: { metrics: Record<string, unknown> }) {
  const hasNullCorrelation = metrics.pearson === null || metrics.spearman === null;
  return (
    <div className="metrics-panel">
      <div className="metrics-summary">
        <Metric label="MAE" value={fixed(metrics.mae as number)} />
        <Metric label="RMSE" value={fixed(metrics.rmse as number)} />
        <Metric label="Pearson" value={correlation(metrics.pearson)} />
        <Metric label="Spearman" value={correlation(metrics.spearman)} />
        <Metric label="n" value={metrics.n as number} />
      </div>
      {hasNullCorrelation && <p className="muted">{CORRELATION_NOTE}</p>}
    </div>
  );
}
