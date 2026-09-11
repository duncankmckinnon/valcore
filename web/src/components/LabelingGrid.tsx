// The annotation queue: a keyboard-driven table over one label set's rows. j/k move
// focus, a accepts the suggestion, u clears the annotation, 1-9 toggle a categorical
// label on/off (multi-select, not replace), ? shows help, Enter opens the focused row's
// full page. Every change saves immediately with an optimistic update that rolls back on
// failure. Row rendering lives in LabelingRow; this file owns fetching, pagination, the
// expanded set, the keyboard handler, and every mutation.
//
// Exported here (rather than as its own file) because the spec treats this as the
// existing LabelingGrid generalized to annotations, not a new component; `AnnotationQueue`
// is the name AnnotationsPage imports it under.

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { annotations, datasets, labelSets } from "../api/client";
import type { AnnotationPut, AnnotationRow, LabelSetProgress } from "../api/types";
import LabelingRow from "./LabelingRow";
import { Button, ConfirmDialog, ErrorBanner, Modal, Spinner } from "./ui";

type Props = {
  datasetId: string;
  labelSetId: string;
};

const PAGE_SIZE = 100;

export default function AnnotationQueue({ datasetId, labelSetId }: Props) {
  const navigate = useNavigate();
  const [labelSet, setLabelSet] = useState<LabelSetProgress | null>(null);
  const [columns, setColumns] = useState<string[]>([]);
  const [rows, setRows] = useState<AnnotationRow[]>([]);
  const [total, setTotal] = useState(0);
  const [annotatedCount, setAnnotatedCount] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [focusedIdx, setFocusedIdx] = useState(0);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [showHelp, setShowHelp] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<AnnotationRow | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<unknown>(null);
  const focusedRef = useRef<HTMLTableRowElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    labelSets
      .list(datasetId)
      .then((items) => setLabelSet(items.find((item) => item.id === labelSetId) ?? null))
      .catch(setError);
  }, [datasetId, labelSetId]);

  useEffect(() => {
    datasets.get(datasetId).then((d) => setColumns(d.columns)).catch(setError);
  }, [datasetId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    labelSets
      .rows(labelSetId, { limit: PAGE_SIZE, offset })
      .then((page) => {
        if (cancelled) return;
        setRows(page.rows);
        setTotal(page.total);
        setAnnotatedCount(page.annotated_count);
        setFocusedIdx(0);
      })
      .catch((err) => {
        if (!cancelled) setError(err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [labelSetId, offset]);

  useEffect(() => {
    const el = focusedRef.current;
    if (el && typeof el.scrollIntoView === "function") {
      try {
        el.scrollIntoView({ block: "nearest" });
      } catch {
        // scrollIntoView is unimplemented under jsdom; safe to ignore.
      }
    }
  }, [focusedIdx]);

  const toggleExpanded = useCallback((key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const applyPut = useCallback(
    async (row: AnnotationRow, body: AnnotationPut) => {
      const hadAnnotation = row.annotation !== null;
      const snapshot = rows;
      setRows((prev) =>
        prev.map((r) =>
          r.row_id === row.row_id
            ? {
                ...r,
                annotation: {
                  id: r.annotation?.id ?? "",
                  label_set_id: labelSetId,
                  dataset_row_id: r.row_id,
                  labels: body.labels ?? [],
                  value: body.value ?? null,
                  suggested_labels: r.annotation?.suggested_labels ?? null,
                  suggested_value: r.annotation?.suggested_value ?? null,
                  source: "manual",
                  reasoning: r.annotation?.reasoning ?? null,
                  description: body.description ?? null,
                },
              }
            : r,
        ),
      );
      try {
        const saved = await annotations.put(labelSetId, row.row_id, body);
        setRows((prev) => prev.map((r) => (r.row_id === row.row_id ? { ...r, annotation: saved } : r)));
        if (!hadAnnotation) setAnnotatedCount((c) => c + 1);
      } catch (err) {
        setRows(snapshot);
        setError(err);
      }
    },
    [rows, labelSetId],
  );

  const toggleLabel = useCallback(
    (row: AnnotationRow, name: string) => {
      const current = row.annotation?.labels ?? [];
      const next = current.includes(name) ? current.filter((l) => l !== name) : [...current, name];
      applyPut(row, {
        labels: next,
        value: row.annotation?.value ?? null,
        description: row.annotation?.description ?? null,
      });
    },
    [applyPut],
  );

  const setValue = useCallback(
    (row: AnnotationRow, value: number) => {
      applyPut(row, {
        labels: row.annotation?.labels ?? [],
        value,
        description: row.annotation?.description ?? null,
      });
    },
    [applyPut],
  );

  const setDescription = useCallback(
    (row: AnnotationRow, description: string) => {
      applyPut(row, {
        labels: row.annotation?.labels ?? [],
        value: row.annotation?.value ?? null,
        description,
      });
    },
    [applyPut],
  );

  const clearAnnotation = useCallback(
    async (row: AnnotationRow) => {
      if (row.annotation === null) return;
      const snapshot = rows;
      setRows((prev) => prev.map((r) => (r.row_id === row.row_id ? { ...r, annotation: null } : r)));
      try {
        await annotations.remove(labelSetId, row.row_id);
        setAnnotatedCount((c) => Math.max(0, c - 1));
      } catch (err) {
        setRows(snapshot);
        setError(err);
      }
    },
    [rows, labelSetId],
  );

  const acceptSuggestion = useCallback(
    async (row: AnnotationRow) => {
      const a = row.annotation;
      if (!a) return;
      const hasSuggestion =
        labelSet?.kind === "categorical"
          ? (a.suggested_labels?.length ?? 0) > 0
          : a.suggested_value !== null && a.suggested_value !== undefined;
      if (!hasSuggestion) return;
      const snapshot = rows;
      setRows((prev) =>
        prev.map((r) =>
          r.row_id === row.row_id
            ? { ...r, annotation: { ...a, labels: a.suggested_labels ?? [], value: a.suggested_value ?? null, source: "accepted" } }
            : r,
        ),
      );
      try {
        const saved = await annotations.accept(labelSetId, row.row_id);
        setRows((prev) => prev.map((r) => (r.row_id === row.row_id ? { ...r, annotation: saved } : r)));
      } catch (err) {
        setRows(snapshot);
        setError(err);
      }
    },
    [rows, labelSetId, labelSet],
  );

  const setCell = useCallback(
    async (row: AnnotationRow, column: string, value: string) => {
      const snapshot = rows;
      setRows((prev) =>
        prev.map((r) => (r.row_id === row.row_id ? { ...r, data: { ...r.data, [column]: value } } : r)),
      );
      try {
        const updated = await datasets.patchRowData(row.row_id, { data: { [column]: value } });
        setRows((prev) =>
          prev.map((r) => (r.row_id === row.row_id ? { ...r, data: updated.data } : r)),
        );
      } catch (err) {
        setRows(snapshot);
        setError(err);
      }
    },
    [rows],
  );

  const openRow = useCallback(
    (row: AnnotationRow) => {
      navigate(`/annotations/${datasetId}/${labelSetId}/rows/${row.row_id}`, { state: { rows } });
    },
    [navigate, datasetId, labelSetId, rows],
  );

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;

      if (event.key === "?") {
        setShowHelp((open) => !open);
        event.preventDefault();
        return;
      }
      if (event.key === "Escape") {
        setShowHelp(false);
        return;
      }
      if (event.key === "j") {
        setFocusedIdx((i) => Math.min(i + 1, rows.length - 1));
        event.preventDefault();
        return;
      }
      if (event.key === "k") {
        setFocusedIdx((i) => Math.max(i - 1, 0));
        event.preventDefault();
        return;
      }

      const row = rows[focusedIdx];
      if (!row) return;

      if (event.key === "Enter") {
        openRow(row);
        event.preventDefault();
      } else if (event.key === "a") {
        acceptSuggestion(row);
        event.preventDefault();
      } else if (event.key === "u") {
        clearAnnotation(row);
        event.preventDefault();
      } else if (labelSet?.kind === "categorical" && /^[1-9]$/.test(event.key)) {
        const options = labelSet.labels ?? [];
        const index = Number(event.key) - 1;
        if (index < options.length) toggleLabel(row, options[index].name);
        event.preventDefault();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rows, focusedIdx, labelSet, acceptSuggestion, clearAnnotation, toggleLabel, openRow]);

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      await datasets.deleteRow(deleteTarget.row_id);
      setRows((prev) => prev.filter((r) => r.row_id !== deleteTarget.row_id));
      setTotal((t) => Math.max(0, t - 1));
      setFocusedIdx((i) => Math.min(i, Math.max(0, rows.length - 2)));
      setDeleteTarget(null);
    } catch (err) {
      setDeleteError(err);
    } finally {
      setDeleteBusy(false);
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  if (loading) return <Spinner />;

  if (labelSet === null) {
    return (
      <div className="labeling-grid">
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
        {!error && <p className="muted">This label set was not found.</p>}
      </div>
    );
  }

  return (
    <div className="labeling-grid" ref={containerRef}>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <div className="labeling-toolbar">
        <span className="muted">{annotatedCount} of {total} annotated</span>
        <Button variant="secondary" onClick={() => setShowHelp(true)}>
          Shortcuts (?)
        </Button>
      </div>

      <table className="table labeling-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
            <th>Labels</th>
            <th>Suggested</th>
            <th>Source</th>
            <th>Description</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <LabelingRow
              key={row.row_id}
              ref={index === focusedIdx ? focusedRef : null}
              row={row}
              columns={columns}
              labelSet={labelSet}
              focused={index === focusedIdx}
              expanded={expanded}
              onToggleExpanded={toggleExpanded}
              onFocus={() => setFocusedIdx(index)}
              onToggleLabel={(name) => toggleLabel(row, name)}
              onSetValue={(value) => setValue(row, value)}
              onClearAnnotation={() => clearAnnotation(row)}
              onAcceptSuggestion={() => acceptSuggestion(row)}
              onSetDescription={(description) => setDescription(row, description)}
              onSetCell={(column, value) => setCell(row, column, value)}
              onOpen={() => openRow(row)}
              onDelete={() => setDeleteTarget(row)}
            />
          ))}
        </tbody>
      </table>

      <div className="labeling-pagination">
        <Button
          variant="secondary"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
        >
          Previous
        </Button>
        <span className="muted">
          Page {currentPage} of {totalPages}
        </span>
        <Button
          variant="secondary"
          disabled={offset + PAGE_SIZE >= total}
          onClick={() => setOffset(offset + PAGE_SIZE)}
        >
          Next
        </Button>
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete row"
        message={`Delete row ${deleteTarget?.idx ?? ""}? This cannot be undone.`}
        busy={deleteBusy}
        error={deleteError}
        onConfirm={confirmDelete}
        onClose={() => {
          setDeleteTarget(null);
          setDeleteError(null);
        }}
      />

      <Modal open={showHelp} title="Keyboard shortcuts" onClose={() => setShowHelp(false)}>
        <ul className="shortcut-list">
          <li><kbd>j</kbd> / <kbd>k</kbd> — move between rows</li>
          <li><kbd>a</kbd> — accept the suggested labels/value</li>
          <li><kbd>1</kbd>–<kbd>9</kbd> — toggle the Nth categorical label</li>
          <li><kbd>u</kbd> — clear the annotation</li>
          <li><kbd>Enter</kbd> — open the focused row's full page</li>
          <li><kbd>?</kbd> — toggle this help</li>
        </ul>
      </Modal>
    </div>
  );
}
