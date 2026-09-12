// web/src/pages/AnnotationRowPage.tsx
// Full-page single-row annotation view: the row's data, every label in the set as a
// checkbox with its criteria shown as reference text (or a bounded number input for a
// numeric set), a rationale textarea, Save, and Prev/Next. Reached by clicking a row (or
// pressing Enter) in the queue grid, which passes its currently-loaded page of rows via
// router state so Prev/Next need no extra fetch; a direct/bookmarked visit falls back to
// fetching that same page itself.

import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { annotations, labelSets } from "../api/client";
import type { AnnotationPut, AnnotationRow, LabelSet } from "../api/types";
import { Button, ErrorBanner, Spinner } from "../components/ui";
import { PageHeader } from "../components/PageHeader";

type Props = {
  datasetId: string;
  labelSetId: string;
  rowId: string;
};

const PAGE_SIZE = 100;

export default function AnnotationRowPage({ datasetId, labelSetId, rowId }: Props) {
  const navigate = useNavigate();
  const location = useLocation();
  const [labelSet, setLabelSet] = useState<LabelSet | null>(null);
  const [entries, setEntries] = useState<AnnotationRow[] | null>(
    (location.state as { rows?: AnnotationRow[] } | null)?.rows ?? null,
  );
  const [labels, setLabels] = useState<string[]>([]);
  const [value, setValueText] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    labelSets.get(labelSetId).then(setLabelSet).catch(setError);
  }, [labelSetId]);

  useEffect(() => {
    if (entries !== null) return;
    labelSets
      .rows(labelSetId, { limit: PAGE_SIZE, offset: 0 })
      .then((page) => setEntries(page.rows))
      .catch(setError);
  }, [labelSetId, entries]);

  const index = entries?.findIndex((r) => r.row_id === rowId) ?? -1;
  const current = index >= 0 && entries ? entries[index] : null;
  const prevId = index > 0 && entries ? entries[index - 1].row_id : null;
  const nextId = entries && index >= 0 && index < entries.length - 1 ? entries[index + 1].row_id : null;

  useEffect(() => {
    if (!current) return;
    setLabels(current.annotation?.labels ?? []);
    setValueText(current.annotation?.value != null ? String(current.annotation.value) : "");
    setDescription(current.annotation?.description ?? "");
  }, [current]);

  function toggleLabel(name: string) {
    setLabels((prev) => (prev.includes(name) ? prev.filter((l) => l !== name) : [...prev, name]));
  }

  async function save() {
    if (!labelSet) return;
    setSaving(true);
    setError(null);
    try {
      const body: AnnotationPut = {
        labels: labelSet.kind === "categorical" ? labels : [],
        value: labelSet.kind === "numeric" ? (value.trim() === "" ? null : Number(value)) : null,
        description: description.trim() === "" ? null : description.trim(),
      };
      const saved = await annotations.put(labelSetId, rowId, body);
      setEntries((prev) =>
        prev ? prev.map((r) => (r.row_id === rowId ? { ...r, annotation: saved } : r)) : prev,
      );
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  }

  function goTo(id: string | null) {
    if (!id) return;
    navigate(`/annotations/${datasetId}/${labelSetId}/rows/${id}`, { state: { rows: entries } });
  }

  if (!labelSet || entries === null) {
    if (error) {
      return (
        <section className="annotation-row-page">
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
        </section>
      );
    }
    return <Spinner />;
  }

  if (!current) {
    return (
      <section className="annotation-row-page">
        <div className="detail-breadcrumb">
          <Link to="/annotations">Annotations</Link> /{" "}
          <Link to={`/annotations/${datasetId}`}>{datasetId}</Link> /{" "}
          <Link to={`/annotations/${datasetId}/${labelSetId}`}>{labelSet.name}</Link>
        </div>
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
        <p className="muted">This row was not found in the label set's queue.</p>
      </section>
    );
  }

  return (
    <section className="annotation-row-page">
      <div className="detail-breadcrumb">
        <Link to="/annotations">Annotations</Link> /{" "}
        <Link to={`/annotations/${datasetId}`}>{datasetId}</Link> /{" "}
        <Link to={`/annotations/${datasetId}/${labelSetId}`}>{labelSet.name}</Link> / row{" "}
        {current.idx}
      </div>

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <PageHeader
        title={`Row ${current.idx}`}
        description={labelSet.description || undefined}
        action={
          <div className="form-actions">
            <Button variant="secondary" disabled={!prevId} onClick={() => goTo(prevId)}>
              ← Prev
            </Button>
            <Button variant="secondary" disabled={!nextId} onClick={() => goTo(nextId)}>
              Next →
            </Button>
          </div>
        }
      />

      <div className="annotation-row-data">
        {Object.entries(current.data).map(([column, val]) => (
          <div key={column} className="field">
            <span className="field-label">{column}</span>
            <p className="generation-text">{String(val)}</p>
          </div>
        ))}
      </div>

      <div className="annotation-row-labels">
        {labelSet.kind === "categorical" ? (
          (labelSet.labels ?? []).map((label) => (
            <label key={label.name} className="field-inline">
              <input
                type="checkbox"
                checked={labels.includes(label.name)}
                onChange={() => toggleLabel(label.name)}
              />
              <strong>{label.name}</strong>
              {label.description && (
                <span className="muted"> — <span>{label.description}</span></span>
              )}
            </label>
          ))
        ) : (
          <label className="field">
            <span className="field-label">
              Value ({labelSet.minimum ?? "unbounded"}–{labelSet.maximum ?? "unbounded"})
            </span>
            <input
              className="select"
              type="number"
              value={value}
              onChange={(e) => setValueText(e.target.value)}
            />
          </label>
        )}
      </div>

      <label className="field">
        <span className="field-label">Description / rationale</span>
        <textarea
          className="textarea"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>

      <div className="form-actions">
        <Button onClick={save} disabled={saving}>
          {saving ? <Spinner /> : "Save"}
        </Button>
      </div>
    </section>
  );
}
