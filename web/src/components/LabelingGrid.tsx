// The hand-labeling surface. A plain table wired for keyboard-driven labeling:
// j/k move focus, a accepts the suggestion, 1-9 apply categorical labels, u clears,
// ? shows help. Every change is saved immediately via patchRow with an optimistic
// update that rolls back on failure. Rows are hand-authorable: cells are editable,
// an Add row control appends a blank row, and each row can be deleted.
// Row rendering lives in LabelingRow; this file owns fetching, pagination, the
// expanded set, the keyboard handler, applyPatch, and the add-row control.

import { useCallback, useEffect, useRef, useState } from "react";
import { datasets } from "../api/client";
import type { DatasetRow, LabelSchema, RowPatch } from "../api/types";
import LabelingRow from "./LabelingRow";
import { Button, ErrorBanner, Modal, Spinner } from "./ui";

type Props = {
  datasetId: string;
  columns: string[];
  schema: LabelSchema;
  onChange?: () => void;
};

const PAGE_SIZE = 100;

export default function LabelingGrid({ datasetId, columns, schema, onChange }: Props) {
  const [rows, setRows] = useState<DatasetRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [focusedIdx, setFocusedIdx] = useState(0);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [showHelp, setShowHelp] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DatasetRow | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<unknown>(null);
  const [focusRowId, setFocusRowId] = useState<string | null>(null);
  const focusedRef = useRef<HTMLTableRowElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    datasets
      .rows(datasetId, { limit: PAGE_SIZE, offset })
      .then((page) => {
        if (cancelled) return;
        setRows(page.rows);
        setTotal(page.total);
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
  }, [datasetId, offset]);

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

  // Move keyboard focus into a freshly added row's first cell once it renders.
  useEffect(() => {
    if (!focusRowId) return;
    const el = containerRef.current?.querySelector<HTMLElement>(
      `[data-row-id="${focusRowId}"] .cell-input, [data-row-id="${focusRowId}"] .cell-expand`,
    );
    el?.focus();
    setFocusRowId(null);
  }, [focusRowId, rows]);

  const applyPatch = useCallback(
    async (rowId: string, patch: RowPatch, optimistic: (row: DatasetRow) => DatasetRow) => {
      const snapshot = rows;
      setRows((prev) => prev.map((r) => (r.id === rowId ? optimistic(r) : r)));
      try {
        const updated = await datasets.patchRow(rowId, patch);
        setRows((prev) => prev.map((r) => (r.id === rowId ? updated : r)));
        onChange?.();
      } catch (err) {
        setRows(snapshot);
        setError(err);
      }
    },
    [rows, onChange],
  );

  const acceptSuggestion = useCallback(
    (row: DatasetRow) => {
      if (!row.suggested_label) return;
      applyPatch(row.id, { accept_suggestion: true }, (r) => ({
        ...r,
        label: r.suggested_label,
        label_source: "accepted",
      }));
    },
    [applyPatch],
  );

  const setLabel = useCallback(
    (row: DatasetRow, value: string | number) => {
      applyPatch(row.id, { label: value }, (r) => ({
        ...r,
        label: { value },
        label_source: "manual",
      }));
    },
    [applyPatch],
  );

  const clearLabel = useCallback(
    (row: DatasetRow) => {
      applyPatch(row.id, { clear_label: true }, (r) => ({
        ...r,
        label: null,
        label_source: null,
      }));
    },
    [applyPatch],
  );

  const setNote = useCallback(
    (row: DatasetRow, note: string) => {
      applyPatch(row.id, { note }, (r) => ({ ...r, note }));
    },
    [applyPatch],
  );

  const setCell = useCallback(
    (row: DatasetRow, column: string, value: string) => {
      applyPatch(row.id, { data: { [column]: value } }, (r) => ({
        ...r,
        data: { ...r.data, [column]: value },
      }));
    },
    [applyPatch],
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

      if (event.key === "a") {
        acceptSuggestion(row);
        event.preventDefault();
      } else if (event.key === "u") {
        clearLabel(row);
        event.preventDefault();
      } else if (schema.kind === "categorical" && /^[1-9]$/.test(event.key)) {
        const labels = schema.labels ?? [];
        const index = Number(event.key) - 1;
        if (index < labels.length) setLabel(row, labels[index]);
        event.preventDefault();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rows, focusedIdx, schema, acceptSuggestion, clearLabel, setLabel]);

  const toggleExpanded = useCallback((key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  async function addRow() {
    const blank: Record<string, unknown> = {};
    for (const column of columns) blank[column] = "";
    try {
      const created = await datasets.addRows(datasetId, [blank]);
      setRows((prev) => [...prev, ...created]);
      setTotal((t) => t + created.length);
      if (created.length > 0) {
        setFocusedIdx(rows.length);
        setFocusRowId(created[0].id);
      }
      onChange?.();
    } catch (err) {
      setError(err);
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      await datasets.deleteRow(deleteTarget.id);
      setRows((prev) => prev.filter((r) => r.id !== deleteTarget.id));
      setTotal((t) => Math.max(0, t - 1));
      setDeleteTarget(null);
      onChange?.();
    } catch (err) {
      setDeleteError(err);
    } finally {
      setDeleteBusy(false);
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  if (loading) return <Spinner />;

  return (
    <div className="labeling-grid" ref={containerRef}>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <div className="labeling-toolbar">
        <span className="muted">
          Rows {rows.length === 0 ? 0 : offset + 1}–{offset + rows.length} of {total}
        </span>
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
            <th>Suggested</th>
            <th>Label</th>
            <th>Source</th>
            <th>Note</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <LabelingRow
              key={row.id}
              ref={index === focusedIdx ? focusedRef : null}
              row={row}
              columns={columns}
              schema={schema}
              focused={index === focusedIdx}
              expanded={expanded}
              onToggleExpanded={toggleExpanded}
              onFocus={() => setFocusedIdx(index)}
              onSetLabel={(value) => setLabel(row, value)}
              onClearLabel={() => clearLabel(row)}
              onAcceptSuggestion={() => acceptSuggestion(row)}
              onSetNote={(note) => setNote(row, note)}
              onSetCell={(column, value) => setCell(row, column, value)}
              onDelete={() => setDeleteTarget(row)}
            />
          ))}
        </tbody>
      </table>

      <div className="add-row-bar">
        <Button variant="secondary" onClick={addRow}>
          Add row
        </Button>
      </div>

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

      <Modal
        open={deleteTarget !== null}
        title="Delete row"
        onClose={() => {
          setDeleteTarget(null);
          setDeleteError(null);
        }}
      >
        <p className="confirm-dialog-message">
          Delete row {deleteTarget?.idx}? This cannot be undone.
        </p>
        <ErrorBanner error={deleteError} />
        <div className="form-actions">
          <Button
            variant="secondary"
            onClick={() => {
              setDeleteTarget(null);
              setDeleteError(null);
            }}
          >
            Cancel
          </Button>
          <Button variant="danger" onClick={confirmDelete} disabled={deleteBusy}>
            {deleteBusy ? <Spinner /> : "Delete"}
          </Button>
        </div>
      </Modal>

      <Modal open={showHelp} title="Keyboard shortcuts" onClose={() => setShowHelp(false)}>
        <ul className="shortcut-list">
          <li>
            <kbd>j</kbd> / <kbd>k</kbd> — move between rows
          </li>
          <li>
            <kbd>a</kbd> — accept the suggested label
          </li>
          <li>
            <kbd>1</kbd>–<kbd>9</kbd> — apply the Nth categorical label
          </li>
          <li>
            <kbd>u</kbd> — clear the label
          </li>
          <li>
            <kbd>?</kbd> — toggle this help
          </li>
        </ul>
      </Modal>
    </div>
  );
}
