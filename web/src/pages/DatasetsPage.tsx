// Datasets index: a progress list plus one creation modal spanning the three
// authoring paths (blank, upload, generate). When the route carries an :id, the
// single-dataset detail view is shown instead.

import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { datasets } from "../api/client";
import type { Dataset, DatasetStats } from "../api/types";
import { Badge, Button, ErrorBanner, Modal, Spinner, Table } from "../components/ui";
import DatasetBlankForm from "../components/DatasetBlankForm";
import DatasetGenerateForm from "../components/DatasetGenerateForm";
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
        {mode === "generate" && <DatasetGenerateForm onCreated={onCreated} />}
      </Modal>
    </section>
  );
}
