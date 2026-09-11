// Datasets index: a progress list plus one creation modal spanning the four
// authoring paths (blank, upload, generate, logfire). When the route carries an :id, the
// single-dataset detail view is shown instead.

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { datasets, labelSets } from "../api/client";
import type { AnnotationLabel, DatasetSummary, LabelSchema } from "../api/types";
import { Badge, Button, ErrorBanner, Modal, Spinner, Table } from "../components/ui";
import { PageHeader } from "../components/PageHeader";
import { EmptyState } from "../components/EmptyState";
import { DatasetIcon } from "../components/icons";
import { useSetup } from "../components/useSetup";
import DatasetBlankForm from "../components/DatasetBlankForm";
import DatasetGenerateForm from "../components/DatasetGenerateForm";
import type { GenerateFormInitial } from "../components/DatasetGenerateForm";
import DatasetLogfireForm from "../components/DatasetLogfireForm";
import DatasetUpload from "../components/DatasetUpload";
import DatasetDetail from "./DatasetDetail";

type CreateMode = "blank" | "upload" | "generate" | "logfire";

const MODES: { id: CreateMode; label: string }[] = [
  { id: "blank", label: "Blank" },
  { id: "upload", label: "Upload" },
  { id: "generate", label: "Generate" },
  { id: "logfire", label: "Logfire" },
];

export default function DatasetsPage() {
  const { id } = useParams();
  if (id) return <DatasetDetail datasetId={id} />;
  return <DatasetsList />;
}

function DatasetsList() {
  const navigate = useNavigate();
  const { status } = useSetup();
  const datasetsUrl = status?.logfire_datasets_url ?? null;
  const [listings, setListings] = useState<DatasetSummary[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [creating, setCreating] = useState(false);
  const [mode, setMode] = useState<CreateMode>("blank");
  // Prefill for the generate form, plus a counter that forces it to remount so the new
  // prefill is picked up (the form reads `initial` once, at mount).
  const [seed, setSeed] = useState<GenerateFormInitial | undefined>(undefined);
  const [seedEpoch, setSeedEpoch] = useState(0);

  // The per-dataset row_count and labeled_count now ride on the list response, so the
  // summary strip and table columns need no per-row stats fetch.
  const load = useCallback(() => {
    setError(null);
    datasets.list().then(setListings).catch(setError);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function openCreate() {
    setMode("blank");
    setSeed(undefined);
    setSeedEpoch((epoch) => epoch + 1);
    setCreating(true);
  }

  async function duplicate(dataset: DatasetSummary) {
    // Settings are optional: a dataset that was uploaded rather than generated still
    // seeds its shape and description, just with nothing to steer content.
    const generation = await datasets.generation(dataset.id).catch(() => null);
    // The dataset's oldest label set (if any) is what continued generation and the old
    // labeled-count reporting track server-side (`Store.primary_label_set`); seed the
    // form from that one.
    const primary = await labelSets
      .list(dataset.id)
      .then((sets) => sets[0] ?? null)
      .catch(() => null);
    const labelSchema: LabelSchema | undefined =
      primary === null
        ? undefined
        : {
            kind: primary.kind,
            labels: primary.kind === "categorical" ? (primary.labels ?? []).map((l: AnnotationLabel) => l.name) : null,
            minimum: primary.minimum,
            maximum: primary.maximum,
          };
    setSeed({
      name: `${dataset.name} copy`,
      description: dataset.description,
      columns: dataset.columns,
      labelSchema,
      instructions: generation?.instructions ?? undefined,
      columnNotes: generation?.column_notes ?? undefined,
      labelMix: generation?.label_mix ?? null,
      count: generation?.count,
    });
    setSeedEpoch((epoch) => epoch + 1);
    setMode("generate");
    setCreating(true);
  }

  function onCreated(datasetId: string) {
    setCreating(false);
    navigate(`/datasets/${datasetId}`);
  }

  // Workspace-wide totals summed from the per-dataset counts on the list response.
  // Percent labeled is a whole percentage; with no rows anywhere it is undefined and
  // renders an em dash rather than NaN%.
  const summary = useMemo(() => {
    const items = listings ?? [];
    const totalRows = items.reduce((sum, dataset) => sum + dataset.row_count, 0);
    const totalLabeled = items.reduce((sum, dataset) => sum + dataset.labeled_count, 0);
    const percent = totalRows === 0 ? "—" : `${Math.round((totalLabeled / totalRows) * 100)}%`;
    return { count: items.length, totalRows, percent };
  }, [listings]);

  return (
    <section>
      <PageHeader
        title="Datasets"
        description="Labeled examples an evaluator is scored against — generated, imported from CSV, pulled from Logfire, or derived from an existing evaluator."
        action={
          <div className="form-actions">
            {datasetsUrl ? (
              <a
                className="btn btn-secondary"
                href={datasetsUrl}
                target="_blank"
                rel="noreferrer"
              >
                Open in Logfire
              </a>
            ) : null}
            <Button onClick={openCreate}>New dataset</Button>
          </div>
        }
      />

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {listings === null ? (
        <Spinner />
      ) : (
        <>
          {listings.length > 0 && (
            <div className="summary-strip">
              <div className="stat">
                <span className="stat-value">{summary.count}</span>
                <span className="stat-label">datasets</span>
              </div>
              <div className="stat">
                <span className="stat-value">{summary.totalRows}</span>
                <span className="stat-label">total rows</span>
              </div>
              <div className="stat">
                <span className="stat-value">{summary.percent}</span>
                <span className="stat-label">labeled</span>
              </div>
            </div>
          )}

          <Table
            rows={listings}
            rowKey={(dataset) => dataset.id}
            empty={
              <EmptyState
                icon={<DatasetIcon size={32} />}
                message="A dataset is rows plus a label space that an evaluator is measured against."
                action={<Button onClick={openCreate}>Create dataset</Button>}
              />
            }
            columns={[
              {
                header: "Name",
                cell: (dataset) => <Link to={`/datasets/${dataset.id}`}>{dataset.name}</Link>,
              },
              {
                header: "Rows",
                cell: (dataset) => dataset.row_count,
              },
              {
                header: "Labeled",
                cell: (dataset) =>
                  dataset.labeled_count === dataset.row_count && dataset.row_count > 0 ? (
                    <Badge tone="success">complete</Badge>
                  ) : (
                    `${dataset.labeled_count} / ${dataset.row_count}`
                  ),
              },
              {
                header: "",
                cell: (dataset) => (
                  <Button
                    variant="secondary"
                    aria-label={`Duplicate ${dataset.name}`}
                    onClick={() => duplicate(dataset)}
                  >
                    Duplicate
                  </Button>
                ),
              },
            ]}
          />
        </>
      )}

      {/* size="lg" because the Generate mode renders a `modal-two-pane` grid: at the default
          `md` (560px) the 1.4fr form column is ~326px, which truncates the column-note inputs.
          Matches the evaluator creation modal, which is lg for the same two-pane reason. */}
      <Modal open={creating} title="New dataset" size="lg" onClose={() => setCreating(false)}>
        <div className="mode-tabs" role="tablist" aria-label="Creation mode">
          {MODES.map((entry) => (
            <button
              key={entry.id}
              role="tab"
              aria-selected={mode === entry.id}
              className={`mode-tab ${mode === entry.id ? "mode-tab-active" : ""}`.trim()}
              onClick={() => setMode(entry.id)}
            >
              {entry.label}
            </button>
          ))}
        </div>

        {mode === "blank" && <DatasetBlankForm onCreated={onCreated} />}
        {mode === "upload" && (
          <DatasetUpload onCreated={(created) => onCreated(created.dataset.id)} />
        )}
        {mode === "generate" && (
          <DatasetGenerateForm key={seedEpoch} onCreated={onCreated} initial={seed} />
        )}
        {mode === "logfire" && <DatasetLogfireForm onCreated={onCreated} />}
      </Modal>
    </section>
  );
}
