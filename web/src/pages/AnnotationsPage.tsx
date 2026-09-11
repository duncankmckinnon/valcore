// web/src/pages/AnnotationsPage.tsx
// Annotations index: pick a dataset, then browse or create its label sets. Clicking a
// label set opens its queue (Task 5); the full-page single-row view (Task 6) nests one
// level deeper. Mirrors DatasetsPage's list-then-detail dispatch, one level deeper.

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { datasets, labelSets } from "../api/client";
import type { DatasetSummary, LabelSetCreate, LabelSetProgress } from "../api/types";
import { Button, ErrorBanner, Modal, Spinner, Table } from "../components/ui";
import { PageHeader } from "../components/PageHeader";
import { EmptyState } from "../components/EmptyState";
import { AnnotationIcon } from "../components/icons";
import LabelSetEditor from "../components/LabelSetEditor";
import AnnotationQueue from "../components/LabelingGrid";
import AnnotationRowPage from "./AnnotationRowPage";

const BLANK_LABEL_SET: LabelSetCreate = {
  name: "",
  description: "",
  kind: "categorical",
  labels: [],
  minimum: null,
  maximum: null,
};

export default function AnnotationsPage() {
  const { datasetId, labelSetId, rowId } = useParams();
  if (datasetId && labelSetId && rowId) {
    return <AnnotationRowPage datasetId={datasetId} labelSetId={labelSetId} rowId={rowId} />;
  }
  if (datasetId && labelSetId) return <AnnotationQueue datasetId={datasetId} labelSetId={labelSetId} />;
  if (datasetId) return <LabelSetsList datasetId={datasetId} />;
  return <AnnotationDatasetsList />;
}

function AnnotationDatasetsList() {
  const [listings, setListings] = useState<DatasetSummary[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    datasets.list().then(setListings).catch(setError);
  }, []);

  return (
    <section>
      <PageHeader
        title="Annotations"
        description="Pick a dataset to browse or create the label sets annotators work through."
      />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {listings === null ? (
        <Spinner />
      ) : (
        <Table
          rows={listings}
          rowKey={(dataset) => dataset.id}
          empty={
            <EmptyState
              icon={<AnnotationIcon size={32} />}
              message="Create a dataset first, then come back here to annotate it."
            />
          }
          columns={[
            {
              header: "Dataset",
              cell: (dataset) => <Link to={`/annotations/${dataset.id}`}>{dataset.name}</Link>,
            },
            { header: "Rows", cell: (dataset) => dataset.row_count },
          ]}
        />
      )}
    </section>
  );
}

function LabelSetsList({ datasetId }: { datasetId: string }) {
  const [items, setItems] = useState<LabelSetProgress[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<LabelSetCreate>(BLANK_LABEL_SET);
  const [submitting, setSubmitting] = useState(false);
  const [createError, setCreateError] = useState<unknown>(null);

  const load = useCallback(() => {
    setError(null);
    labelSets.list(datasetId).then(setItems).catch(setError);
  }, [datasetId]);

  useEffect(() => {
    load();
  }, [load]);

  function openCreate() {
    setDraft(BLANK_LABEL_SET);
    setCreateError(null);
    setCreating(true);
  }

  async function submitCreate() {
    setSubmitting(true);
    setCreateError(null);
    try {
      await labelSets.create(datasetId, draft);
      setCreating(false);
      load();
    } catch (err) {
      setCreateError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section>
      <div className="detail-breadcrumb">
        <Link to="/annotations">Annotations</Link> / {datasetId}
      </div>
      <PageHeader
        title="Label sets"
        description="Each label set is an annotation contract: what an annotator is asked to record for every row."
        action={<Button onClick={openCreate}>New label set</Button>}
      />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {items === null ? (
        <Spinner />
      ) : (
        <Table
          rows={items}
          rowKey={(item) => item.id}
          empty={
            <EmptyState
              icon={<AnnotationIcon size={32} />}
              message="No label sets yet."
              action={<Button onClick={openCreate}>Create a label set</Button>}
            />
          }
          columns={[
            {
              header: "Name",
              cell: (item) => <Link to={`/annotations/${datasetId}/${item.id}`}>{item.name}</Link>,
            },
            { header: "Kind", cell: (item) => item.kind },
            {
              header: "Progress",
              cell: (item) => `${item.annotated_count} / ${item.row_count} annotated`,
            },
          ]}
        />
      )}

      <Modal open={creating} title="New label set" onClose={() => setCreating(false)}>
        <ErrorBanner error={createError} onDismiss={() => setCreateError(null)} />
        <LabelSetEditor value={draft} onChange={setDraft} />
        <div className="form-actions">
          <Button variant="secondary" onClick={() => setCreating(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={submitCreate} disabled={submitting || draft.name.trim() === ""}>
            {submitting ? <Spinner /> : "Create"}
          </Button>
        </div>
      </Modal>
    </section>
  );
}
