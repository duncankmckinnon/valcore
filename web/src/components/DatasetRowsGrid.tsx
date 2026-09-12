// A plain, editable table over a dataset's rows: view/edit each column's value, add a
// blank row, delete a row, and paginate. Labels live on the separate annotation queue
// reached via "Annotate" -- this grid only ever reads/writes DatasetRow.data.

import { useCallback, useEffect, useState } from "react";
import { datasets } from "../api/client";
import type { DatasetRow } from "../api/types";
import { Button, ConfirmDialog, ErrorBanner, Spinner } from "./ui";

type Props = {
  datasetId: string;
  columns: string[];
  onChange?: () => void;
};

const PAGE_SIZE = 100;
const CELL_TRUNCATE = 80;

function scalar(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value, null, 2);
}

export default function DatasetRowsGrid({ datasetId, columns, onChange }: Props) {
  const [rows, setRows] = useState<DatasetRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [deleteTarget, setDeleteTarget] = useState<DatasetRow | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    datasets
      .rows(datasetId, { limit: PAGE_SIZE, offset })
      .then((page) => {
        if (cancelled) return;
        setRows(page.rows);
        setTotal(page.total);
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

  const toggleExpanded = useCallback((key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const setCell = useCallback(
    async (row: DatasetRow, column: string, value: string) => {
      const snapshot = rows;
      setRows((prev) =>
        prev.map((r) => (r.id === row.id ? { ...r, data: { ...r.data, [column]: value } } : r)),
      );
      try {
        const updated = await datasets.patchRowData(row.id, { data: { [column]: value } });
        setRows((prev) => prev.map((r) => (r.id === row.id ? updated : r)));
      } catch (err) {
        setRows(snapshot);
        setError(err);
      }
    },
    [rows],
  );

  async function addRow() {
    const blank: Record<string, unknown> = {};
    for (const column of columns) blank[column] = "";
    try {
      const created = await datasets.addRows(datasetId, [blank]);
      setRows((prev) => [...prev, ...created]);
      setTotal((t) => t + created.length);
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
    <div className="labeling-grid">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <div className="labeling-toolbar">
        <span className="muted">
          Rows {rows.length === 0 ? 0 : offset + 1}–{offset + rows.length} of {total}
        </span>
      </div>

      <table className="table labeling-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} data-row-id={row.id}>
              {columns.map((column) => {
                const key = `${row.id}:${column}`;
                const display = scalar(row.data[column]);
                const isLong = display.length > CELL_TRUNCATE;

                if (isLong && !expanded.has(key)) {
                  return (
                    <td key={column}>
                      <button
                        type="button"
                        className="cell-expand"
                        onClick={() => toggleExpanded(key)}
                      >
                        {`${display.slice(0, CELL_TRUNCATE)}…`}
                      </button>
                    </td>
                  );
                }

                if (isLong) {
                  return (
                    <td key={column}>
                      <textarea
                        key={display}
                        className="cell-textarea"
                        aria-label={column}
                        defaultValue={display}
                        onBlur={(e) => {
                          if (e.target.value !== display) setCell(row, column, e.target.value);
                        }}
                      />
                    </td>
                  );
                }

                return (
                  <td key={column}>
                    <input
                      key={display}
                      className="cell-input"
                      aria-label={column}
                      defaultValue={display}
                      onBlur={(e) => {
                        if (e.target.value !== display) setCell(row, column, e.target.value);
                      }}
                    />
                  </td>
                );
              })}
              <td>
                <button
                  type="button"
                  className="row-delete"
                  aria-label={`Delete row ${row.idx}`}
                  onClick={() => setDeleteTarget(row)}
                >
                  ×
                </button>
              </td>
            </tr>
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
    </div>
  );
}
