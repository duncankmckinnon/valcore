// Detail view for a single dataset: a stats header over the labeling grid.

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { datasets } from "../api/client";
import type { Dataset, DatasetStats } from "../api/types";
import { ErrorBanner, Spinner } from "../components/ui";
import LabelingGrid from "../components/LabelingGrid";

type Props = {
  datasetId: string;
};

export default function DatasetDetail({ datasetId }: Props) {
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [stats, setStats] = useState<DatasetStats | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    datasets
      .get(datasetId)
      .then((ds) => {
        if (!cancelled) setDataset(ds);
      })
      .catch((err) => {
        if (!cancelled) setError(err);
      });
    return () => {
      cancelled = true;
    };
  }, [datasetId]);

  const refreshStats = useCallback(() => {
    datasets
      .stats(datasetId)
      .then(setStats)
      .catch(() => {
        // Stats are non-critical; a failure leaves the last value in place.
      });
  }, [datasetId]);

  useEffect(() => {
    refreshStats();
  }, [refreshStats]);

  if (error) return <ErrorBanner error={error} />;
  if (!dataset) return <Spinner />;

  return (
    <section>
      <div className="detail-breadcrumb">
        <Link to="/datasets">Datasets</Link> / {dataset.name}
      </div>
      <h1>{dataset.name}</h1>
      {dataset.description && <p className="muted">{dataset.description}</p>}

      {stats && (
        <div className="stats-header">
          <div className="stat">
            <span className="stat-value">{stats.total}</span>
            <span className="stat-label">total</span>
          </div>
          <div className="stat">
            <span className="stat-value">{stats.labeled}</span>
            <span className="stat-label">labeled</span>
          </div>
          <div className="stat">
            <span className="stat-value">{stats.unlabeled}</span>
            <span className="stat-label">unlabeled</span>
          </div>
          <div className="stat-distribution">
            {Object.entries(stats.label_distribution).map(([label, count]) => (
              <span key={label} className="badge">
                {label}: {count}
              </span>
            ))}
          </div>
        </div>
      )}

      <LabelingGrid
        datasetId={datasetId}
        columns={dataset.columns}
        schema={dataset.label_schema}
        onChange={refreshStats}
      />
    </section>
  );
}
