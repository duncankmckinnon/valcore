// Detail view for a single dataset: a stats header with a link to the annotation
// queue, and Edit (shape/settings) and Delete actions in the header.

import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { datasets } from "../api/client";
import type {
  Dataset,
  DatasetGeneration,
  DatasetHostedFetch,
  DatasetLogfirePull,
  DatasetStats,
  GeneratedConfig,
  LogfirePushResult,
} from "../api/types";
import { Button, ConfirmDialog, ErrorBanner, Spinner } from "../components/ui";
import { useSetup } from "../components/useSetup";
import { PageHeader } from "../components/PageHeader";
import { datasetCasesUrl } from "../logfireLinks";
import DatasetRowsGrid from "../components/DatasetRowsGrid";
import DatasetSettingsModal from "../components/DatasetSettingsModal";
import { ExportModal } from "../components/ExportModal";
import EvaluatorFromDataset from "../components/EvaluatorFromDataset";
import GenerateMoreRows from "../components/GenerateMoreRows";
import GenerationSettings from "../components/GenerationSettings";
import LogfirePullMoreRows from "../components/LogfirePullMoreRows";
import { LogfirePullSettings } from "../components/LogfirePullSettings";

// Mirrors the server's generation cap so an over-large ask is refused before it costs a
// slow generation call.
const MAX_GENERATE_COUNT = 200;

type Props = {
  datasetId: string;
};

export default function DatasetDetail({ datasetId }: Props) {
  const navigate = useNavigate();
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [stats, setStats] = useState<DatasetStats | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [generation, setGeneration] = useState<DatasetGeneration | null>(null);
  const [logfirePull, setLogfirePull] = useState<DatasetLogfirePull | null>(null);
  const [hostedFetch, setHostedFetch] = useState<DatasetHostedFetch | null>(null);
  const [editing, setEditing] = useState(false);
  const [generatingRows, setGeneratingRows] = useState(false);
  const [pullingRows, setPullingRows] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncAdded, setSyncAdded] = useState<number | null>(null);
  const [syncError, setSyncError] = useState<unknown>(null);
  const [generatingEvaluator, setGeneratingEvaluator] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<unknown>(null);
  const [pushing, setPushing] = useState(false);
  const [pushResult, setPushResult] = useState<LogfirePushResult | null>(null);
  const [pushError, setPushError] = useState<unknown>(null);

  // Pushing needs the Logfire write key for the valcore project. The read key
  // queries a different project and cannot stand in.
  const { status } = useSetup();
  const logfireKeySet =
    status?.keys.some((key) => key.name === "logfire_write_key" && key.set) ?? false;
  const logfireDatasetUrl =
    status?.logfire_datasets_url && dataset
      ? datasetCasesUrl(status.logfire_datasets_url, dataset.name)
      : null;

  const pushToLogfire = () => {
    setPushing(true);
    setPushError(null);
    setPushResult(null);
    datasets
      .logfirePush(datasetId, { name: dataset?.name })
      .then(setPushResult)
      .catch(setPushError)
      .finally(() => setPushing(false));
  };

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

  const refreshGeneration = useCallback(() => {
    datasets
      .generation(datasetId)
      .then(setGeneration)
      .catch(() => {
        // Provenance is non-critical: a failure just leaves the panel and prefill empty.
      });
  }, [datasetId]);

  useEffect(() => {
    refreshGeneration();
  }, [refreshGeneration]);

  const refreshLogfirePull = useCallback(() => {
    datasets
      .logfirePull(datasetId)
      .then(setLogfirePull)
      .catch(() => {
        // Provenance is non-critical: a failure just leaves the panel empty.
      });
  }, [datasetId]);

  useEffect(() => {
    refreshLogfirePull();
  }, [refreshLogfirePull]);

  useEffect(() => {
    datasets
      .hostedFetch(datasetId)
      .then(setHostedFetch)
      .catch(() => {
        // Provenance is non-critical: a failure just leaves the Sync action hidden.
      });
  }, [datasetId]);

  const syncFromLogfire = () => {
    setSyncing(true);
    setSyncError(null);
    setSyncAdded(null);
    datasets
      .pullMoreFromLogfireHosted(datasetId)
      .then((added) => {
        setSyncAdded(added.length);
        refreshStats();
      })
      .catch(setSyncError)
      .finally(() => setSyncing(false));
  };

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
    // Replace the local shape so the header reflects the rename/reshape immediately,
    // then refresh the counts a column change may have affected.
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

  return (
    <section>
      <div className="detail-breadcrumb">
        <Link to="/datasets">Datasets</Link> / {dataset.name}
      </div>
      {/* Push feedback is inline rather than routed through `error`, which replaces the whole
          page — a failed push must not take the dataset view down with it. */}
      {pushError !== null && <ErrorBanner error={pushError} onDismiss={() => setPushError(null)} />}
      {pushResult !== null && (
        <p className="field-hint" role="status">
          Pushed to Logfire as “{pushResult.name}”
          {pushResult.case_count !== null && ` with ${pushResult.case_count} cases`}.
        </p>
      )}
      {/* Sync feedback is inline for the same reason push's is: a failed or empty sync must
          not take the dataset view down with it. */}
      {syncError !== null && <ErrorBanner error={syncError} onDismiss={() => setSyncError(null)} />}
      {syncAdded !== null && (
        <p className="field-hint" role="status">
          {syncAdded === 0
            ? "Already up to date with the hosted dataset."
            : `Added ${syncAdded} new row${syncAdded === 1 ? "" : "s"} from the hosted dataset.`}
        </p>
      )}
      <PageHeader
        title={dataset.name}
        description={dataset.description || undefined}
        action={
          <div className="form-actions">
            {logfireDatasetUrl ? (
              <a
                className="btn btn-secondary"
                href={logfireDatasetUrl}
                target="_blank"
                rel="noreferrer"
              >
                Open in Logfire
              </a>
            ) : null}
            <Link className="btn btn-secondary" to={`/annotations/${datasetId}`}>
              Annotate
            </Link>
            <Button variant="secondary" onClick={() => setGeneratingRows(true)}>
              Generate more rows
            </Button>
            {logfirePull !== null ? (
              <Button variant="secondary" onClick={() => setPullingRows(true)}>
                Pull more from Logfire
              </Button>
            ) : null}
            {hostedFetch !== null ? (
              <Button variant="secondary" onClick={syncFromLogfire} disabled={syncing}>
                {syncing ? "Syncing…" : "Sync from Logfire"}
              </Button>
            ) : null}
            <Button variant="secondary" onClick={() => setGeneratingEvaluator(true)}>
              Generate evaluator
            </Button>
            <Button variant="secondary" onClick={() => setExporting(true)}>
              Export
            </Button>
            <Button
              variant="secondary"
              onClick={pushToLogfire}
              disabled={pushing || !logfireKeySet}
              title={
                logfireKeySet
                  ? "Publish this dataset to Logfire's hosted dataset store"
                  : "Set the Logfire write key first — see Settings"
              }
            >
              {pushing ? "Pushing…" : "Push to Logfire"}
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
        }
      />

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

      <GenerationSettings generation={generation} />
      <LogfirePullSettings pull={logfirePull} />

      <DatasetRowsGrid datasetId={datasetId} columns={dataset.columns} onChange={refreshStats} />

      <DatasetSettingsModal
        open={editing}
        dataset={dataset}
        onSaved={onSaved}
        onClose={() => setEditing(false)}
      />

      <GenerateMoreRows
        open={generatingRows}
        dataset={dataset}
        generation={generation}
        maxCount={MAX_GENERATE_COUNT}
        onGenerated={() => {
          setGeneratingRows(false);
          // The top-up records the ask that ran, so refetch it for the next prefill.
          refreshGeneration();
          refreshStats();
        }}
        onClose={() => setGeneratingRows(false)}
      />

      {logfirePull !== null && (
        <LogfirePullMoreRows
          open={pullingRows}
          dataset={dataset}
          pull={logfirePull}
          onPulled={() => {
            setPullingRows(false);
            // The top-up records the ask that ran, so refetch it for the next prefill.
            refreshLogfirePull();
            refreshStats();
          }}
          onClose={() => setPullingRows(false)}
        />
      )}

      <EvaluatorFromDataset
        open={generatingEvaluator}
        dataset={dataset}
        onGenerated={onEvaluatorGenerated}
        onClose={() => setGeneratingEvaluator(false)}
      />

      {exporting && (
        <ExportModal
          open={exporting}
          subject={{ kind: "dataset", datasetId }}
          onClose={() => setExporting(false)}
        />
      )}

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
