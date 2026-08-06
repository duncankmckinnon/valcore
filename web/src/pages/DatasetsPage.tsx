// Datasets index: a progress list plus one creation modal spanning the three
// authoring paths (blank, upload, generate). When the route carries an :id, the
// single-dataset detail view is shown instead.

import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { datasets } from "../api/client";
import type { Dataset, DatasetStats, LabelSchema } from "../api/types";
import { Badge, Button, ErrorBanner, Modal, Spinner, Table } from "../components/ui";
import DatasetBlankForm from "../components/DatasetBlankForm";
import DatasetGenerateForm from "../components/DatasetGenerateForm";
import type { GenerateFormInitial } from "../components/DatasetGenerateForm";
import DatasetUpload from "../components/DatasetUpload";
import DatasetDetail from "./DatasetDetail";

type Listing = { dataset: Dataset; stats: DatasetStats | null };

type CreateMode = "blank" | "upload" | "generate";

const MODES: { id: CreateMode; label: string }[] = [
  { id: "blank", label: "Blank" },
  { id: "upload", label: "Upload" },
  { id: "generate", label: "Generate" },
];

export default function DatasetsPage() {
  const { id } = useParams();
  if (id) return <DatasetDetail datasetId={id} />;
  return <DatasetsList />;
}

function DatasetsList() {
  const navigate = useNavigate();
  const [listings, setListings] = useState<Listing[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [creating, setCreating] = useState(false);
  const [mode, setMode] = useState<CreateMode>("blank");
  // Prefill for the generate form, plus a counter that forces it to remount so the new
  // prefill is picked up (the form reads `initial` once, at mount).
  const [seed, setSeed] = useState<GenerateFormInitial | undefined>(undefined);
  const [seedEpoch, setSeedEpoch] = useState(0);

  const load = useCallback(() => {
    setError(null);
    datasets
      .list()
      .then(async (all) => {
        const withStats = await Promise.all(
          all.map(async (dataset) => ({
            dataset,
            stats: await datasets.stats(dataset.id).catch(() => null),
          })),
        );
        setListings(withStats);
      })
      .catch(setError);
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

  async function duplicate(dataset: Dataset) {
    // Settings are optional: a dataset that was uploaded rather than generated still
    // seeds its shape and description, just with nothing to steer content.
    const generation = await datasets.generation(dataset.id).catch(() => null);
    setSeed({
      name: `${dataset.name} copy`,
      description: dataset.description,
      columns: dataset.columns,
      labelSchema: Object.keys(dataset.label_schema).length > 0
        ? (dataset.label_schema as LabelSchema)
        : undefined,
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

  return (
    <section>
      <div className="page-header">
        <h1>Datasets</h1>
        <div className="form-actions">
          <Button onClick={openCreate}>New dataset</Button>
        </div>
      </div>

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {listings === null ? (
        <Spinner />
      ) : (
        <Table
          rows={listings}
          rowKey={(listing) => listing.dataset.id}
          empty="No datasets yet. Create, upload, or generate one to get started."
          columns={[
            {
              header: "Name",
              cell: (listing) => (
                <Link to={`/datasets/${listing.dataset.id}`}>{listing.dataset.name}</Link>
              ),
            },
            {
              header: "Rows",
              cell: (listing) => listing.stats?.total ?? "—",
            },
            {
              header: "Labeled",
              cell: (listing) =>
                listing.stats ? (
                  <Badge tone={listing.stats.unlabeled === 0 ? "success" : "neutral"}>
                    {listing.stats.labeled} / {listing.stats.total}
                  </Badge>
                ) : (
                  "—"
                ),
            },
            {
              header: "",
              cell: (listing) => (
                <Button
                  variant="secondary"
                  aria-label={`Duplicate ${listing.dataset.name}`}
                  onClick={() => duplicate(listing.dataset)}
                >
                  Duplicate
                </Button>
              ),
            },
          ]}
        />
      )}

      <Modal open={creating} title="New dataset" onClose={() => setCreating(false)}>
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
      </Modal>
    </section>
  );
}
