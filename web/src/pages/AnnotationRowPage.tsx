// web/src/pages/AnnotationRowPage.tsx
// Full-page single-row annotation view: the row's data, every label in the set as a
// checkbox with its criteria shown as reference text (or a bounded number input for a
// numeric set), a rationale textarea, Save, and Prev/Next. Reached by clicking a row (or
// pressing Enter) in the queue grid, which passes its currently-loaded page of rows via
// router state so Prev/Next need no extra fetch; a direct/bookmarked visit falls back to
// fetching that same page itself.

import { useEffect, useRef, useState } from "react";
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

function RowContent({ value }: { value: unknown }) {
  try {
    const parsed = typeof value === "string" ? JSON.parse(value) : value;
    return <pre className="generation-text"><code>{JSON.stringify(parsed, null, 2)}</code></pre>;
  } catch {
    return <p className="generation-text">{String(value)}</p>;
  }
}

export default function AnnotationRowPage({ datasetId, labelSetId, rowId }: Props) {
  const navigate = useNavigate();
  const location = useLocation();
  const [labelSet, setLabelSet] = useState<LabelSet | null>(null);
  const [entries, setEntries] = useState<AnnotationRow[] | null>(
    (location.state as { rows?: AnnotationRow[] } | null)?.rows ?? null,
  );
  const [hydratedRowId, setHydratedRowId] = useState<string | null>(null);
  const [labels, setLabels] = useState<string[]>([]);
  const [value, setValueText] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const labelInputs = useRef<(HTMLInputElement | null)[]>([]);

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

  // Seeding the form during render -- rather than from an effect -- is what keeps
  // an edit from being silently discarded. An effect runs *after* the commit that
  // first paints the form, so anything typed or clicked in that window was
  // overwritten when the effect finally flushed; under load (CI) that window is
  // wide enough to lose a real interaction. React re-renders immediately on a
  // render-phase update, so the form is never committed un-seeded.
  // Keying on row_id rather than the `current` object also stops a save's own
  // response -- which replaces `entries`, and so `current` -- from reverting
  // edits the user made while that request was in flight.
  if (current && hydratedRowId !== current.row_id) {
    setHydratedRowId(current.row_id);
    setLabels(current.annotation?.labels ?? []);
    setValueText(current.annotation?.value != null ? String(current.annotation.value) : "");
    setDescription(current.annotation?.description ?? "");
  }

  function toggleLabel(name: string) {
    setLabels((prev) => (prev.includes(name) ? prev.filter((l) => l !== name) : [...prev, name]));
  }

  async function save() {
    if (!labelSet || saving) return;
    setSaving(true);
    setError(null);
    try {
      const body: AnnotationPut = {
        labels: labelSet.kind === "categorical" ? labels : [],
        value: labelSet.kind === "numeric" ? (value.trim() === "" ? null : Number(value)) : null,
        description: description.trim() === "" ? null : description.trim(),
      };
      const saved = await annotations.put(labelSetId, rowId, body);
      const updated = entries?.map((r) => r.row_id === rowId ? { ...r, annotation: saved } : r) ?? null;
      setEntries(updated);
      if (nextId) {
        navigate(`/annotations/${datasetId}/${labelSetId}/rows/${nextId}`, { state: { rows: updated } });
      }
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  }

  function goTo(id: string | null) {
    if (!id || saving) return;
    navigate(`/annotations/${datasetId}/${labelSetId}/rows/${id}`, { state: { rows: entries } });
  }

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || saving) return;
      const target = event.target;
      if (target instanceof HTMLElement &&
          (target.isContentEditable || target.closest("textarea, select, input:not([type=checkbox])"))) return;
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        goTo(event.key === "ArrowLeft" ? prevId : nextId);
      } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        const inputs = labelInputs.current.filter((input): input is HTMLInputElement => input !== null);
        if (!inputs.length) return;
        event.preventDefault();
        const focused = inputs.indexOf(document.activeElement as HTMLInputElement);
        const next = focused < 0
          ? (event.key === "ArrowDown" ? 0 : inputs.length - 1)
          : (focused + (event.key === "ArrowDown" ? 1 : -1) + inputs.length) % inputs.length;
        inputs[next].focus();
      } else if (event.key === "Enter" && labelInputs.current.includes(target as HTMLInputElement)) {
        event.preventDefault();
        (target as HTMLInputElement).click();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  });

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
            <Button variant="secondary" disabled={!prevId || saving} onClick={() => goTo(prevId)}>
              ← Prev
            </Button>
            <Button variant="secondary" disabled={!nextId || saving} onClick={() => goTo(nextId)}>
              Next →
            </Button>
          </div>
        }
      />

      <p className="muted">← / → change rows · ↑ / ↓ focus labels · Enter toggles a label · Save advances to the next row.</p>

      <div className="annotation-row-data">
        {Object.entries(current.data).map(([column, val]) => (
          <div key={column} className="field">
            <span className="field-label">{column}</span>
            <RowContent value={val} />
          </div>
        ))}
      </div>

      <div className="annotation-row-labels">
        {labelSet.kind === "categorical" ? (
          (labelSet.labels ?? []).map((label, index) => (
            <label key={label.name} className="field-inline">
              <input
                ref={(element) => { labelInputs.current[index] = element; }}
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
