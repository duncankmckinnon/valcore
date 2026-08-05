// Detail view for a single dataset: a stats header over the labeling grid, with
// Edit (shape/settings) and Delete actions in the header.

import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { datasets } from "../api/client";
import type { Dataset, DatasetStats, GeneratedConfig, LabelSchema } from "../api/types";
import { Button, ConfirmDialog, ErrorBanner, Spinner } from "../components/ui";
import DatasetSettingsModal from "../components/DatasetSettingsModal";
import EvaluatorFromDataset from "../components/EvaluatorFromDataset";
import LabelingGrid from "../components/LabelingGrid";

type Props = {
  datasetId: string;
};

export default function DatasetDetail({ datasetId }: Props) {
  const navigate = useNavigate();
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [stats, setStats] = useState<DatasetStats | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [editing, setEditing] = useState(false);
  const [generatingEvaluator, setGeneratingEvaluator] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<unknown>(null);

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

  function onSaved(updated: Dataset) {
    setEditing(false);
    // Replace the local shape so the grid headers and label controls re-render,
    // then refresh the counts a migration may have changed.
    setDataset(updated);
    refreshStats();
  }

  function onEvaluatorGenerated(draft: GeneratedConfig) {
    // The result is an editable draft, not a saved version: hand it to the evaluators
    // flow, which already presents generated configs as editable drafts in the version
    // editor. Nothing is persisted from here.
    setGeneratingEvaluator(false);
    navigate("/evaluators", { state: { draft } });
  }

  async function confirmDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await datasets.remove(datasetId);
      navigate("/datasets");
    } catch (err) {
      // A ReferencedError keeps the dialog open with the run count in view.
      setDeleteError(err);
    } finally {
      setDeleting(false);
    }
  }

  if (error) return <ErrorBanner error={error} />;
  if (!dataset) return <Spinner />;

  // Remount the grid whenever the dataset's shape changes so it refetches rows
  // (e.g. after a column migration or a cleared label schema).
  const gridKey = JSON.stringify([dataset.columns, dataset.label_schema]);

  return (
    <section>
      <div className="detail-breadcrumb">
        <Link to="/datasets">Datasets</Link> / {dataset.name}
      </div>
      <div className="detail-header">
        <div>
          <h1>{dataset.name}</h1>
          {dataset.description && <p className="muted">{dataset.description}</p>}
        </div>
        <div className="form-actions">
          <Button variant="secondary" onClick={() => setGeneratingEvaluator(true)}>
            Generate evaluator
          </Button>
          <Button variant="secondary" onClick={() => setEditing(true)}>
            Edit
          </Button>
          <Button
            variant="danger"
            onClick={() => {
              setDeleteError(null);
              setConfirmingDelete(true);
            }}
          >
            Delete dataset
          </Button>
        </div>
      </div>

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
        key={gridKey}
        datasetId={datasetId}
        columns={dataset.columns}
        schema={dataset.label_schema as LabelSchema}
        onChange={refreshStats}
      />

      <DatasetSettingsModal
        open={editing}
        dataset={dataset}
        onSaved={onSaved}
        onClose={() => setEditing(false)}
      />

      <EvaluatorFromDataset
        open={generatingEvaluator}
        dataset={dataset}
        onGenerated={onEvaluatorGenerated}
        onClose={() => setGeneratingEvaluator(false)}
      />

      <ConfirmDialog
        open={confirmingDelete}
        title="Delete dataset"
        message={`Delete "${dataset.name}"? This cannot be undone.`}
        busy={deleting}
        error={deleteError}
        onConfirm={confirmDelete}
        onClose={() => setConfirmingDelete(false)}
      />
    </section>
  );
}
