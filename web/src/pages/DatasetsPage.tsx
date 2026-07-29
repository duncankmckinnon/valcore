// Datasets index: a progress list plus the two creation paths (upload and generate).
// When the route carries an :id, the single-dataset detail view is shown instead.

import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { datasets } from "../api/client";
import type { Dataset, DatasetStats, LabelSchema } from "../api/types";
import { Badge, Button, ErrorBanner, Modal, Spinner, Table } from "../components/ui";
import DatasetUpload from "../components/DatasetUpload";
import LabelSchemaEditor from "../components/LabelSchemaEditor";
import DatasetDetail from "./DatasetDetail";

type Listing = { dataset: Dataset; stats: DatasetStats | null };

const DEFAULT_SCHEMA: LabelSchema = { kind: "categorical", labels: [], minimum: null, maximum: null };

export default function DatasetsPage() {
  const { id } = useParams();
  if (id) return <DatasetDetail datasetId={id} />;
  return <DatasetsList />;
}

function DatasetsList() {
  const navigate = useNavigate();
  const [listings, setListings] = useState<Listing[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [creating, setCreating] = useState<"upload" | "generate" | null>(null);

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

  function onCreated(datasetId: string) {
    setCreating(null);
    navigate(`/datasets/${datasetId}`);
  }

  return (
    <section>
      <div className="page-header">
        <h1>Datasets</h1>
        <div className="form-actions">
          <Button onClick={() => setCreating("upload")}>Upload</Button>
          <Button variant="secondary" onClick={() => setCreating("generate")}>
            Generate
          </Button>
        </div>
      </div>

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {listings === null ? (
        <Spinner />
      ) : (
        <Table
          rows={listings}
          rowKey={(listing) => listing.dataset.id}
          empty="No datasets yet. Upload or generate one to get started."
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

      <Modal
        open={creating === "upload"}
        title="Upload dataset"
        onClose={() => setCreating(null)}
      >
        <DatasetUpload onCreated={(created) => onCreated(created.dataset.id)} />
      </Modal>

      <Modal
        open={creating === "generate"}
        title="Generate dataset"
        onClose={() => setCreating(null)}
      >
        <GenerateForm onCreated={onCreated} />
      </Modal>
    </section>
  );
}

function GenerateForm({ onCreated }: { onCreated: (datasetId: string) => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [columnsText, setColumnsText] = useState("");
  const [count, setCount] = useState(20);
  const [schema, setSchema] = useState<LabelSchema>(DEFAULT_SCHEMA);
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      const columns = columnsText
        .split(",")
        .map((column) => column.trim())
        .filter(Boolean);
      const created = await datasets.generate({
        name: name.trim(),
        description: description.trim(),
        columns,
        label_schema: schema,
        count,
      });
      onCreated(created.dataset.id);
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="generate-form">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <label className="field">
        <span className="field-label">Name</span>
        <input className="select" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        <span className="field-label">Description of the data to generate</span>
        <textarea
          className="textarea"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>
      <label className="field">
        <span className="field-label">Columns (comma separated)</span>
        <input
          className="select"
          value={columnsText}
          placeholder="question, answer"
          onChange={(e) => setColumnsText(e.target.value)}
        />
      </label>
      <label className="field">
        <span className="field-label">Row count</span>
        <input
          className="select"
          type="number"
          min={1}
          max={200}
          value={count}
          onChange={(e) => setCount(Number(e.target.value))}
        />
      </label>
      <LabelSchemaEditor value={schema} onChange={setSchema} />
      <div className="form-actions">
        <Button onClick={submit} disabled={submitting || !description.trim()}>
          {submitting ? <Spinner /> : "Generate"}
        </Button>
      </div>
    </div>
  );
}
