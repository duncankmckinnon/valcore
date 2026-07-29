// The hand-labeling surface. A plain table wired for keyboard-driven labeling:
// j/k move focus, a accepts the suggestion, 1-9 apply categorical labels, u clears,
// ? shows help. Every change is saved immediately via patchRow with an optimistic
// update that rolls back on failure.

import { useCallback, useEffect, useRef, useState } from "react";
import { datasets } from "../api/client";
import type { DatasetRow, LabelSchema, LabelSource, RowPatch } from "../api/types";
import { Badge, Button, ErrorBanner, Modal, Select, Spinner } from "./ui";

type Props = {
  datasetId: string;
  columns: string[];
  schema: LabelSchema;
  onChange?: () => void;
};

const PAGE_SIZE = 100;
const CELL_TRUNCATE = 80;

const SOURCE_TONE: Record<LabelSource, "neutral" | "success" | "warning"> = {
  manual: "neutral",
  accepted: "success",
  generated: "warning",
};

function scalar(value: unknown): string | number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string" || typeof value === "number") return value;
  return String(value);
}

function labelValue(label: Record<string, unknown> | null): string | number | null {
  return label ? scalar(label.value) : null;
}

export default function LabelingGrid({ datasetId, columns, schema, onChange }: Props) {
  const [rows, setRows] = useState<DatasetRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [focusedIdx, setFocusedIdx] = useState(0);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [showHelp, setShowHelp] = useState(false);
  const focusedRef = useRef<HTMLTableRowElement | null>(null);

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

  const applyPatch = useCallback(
    async (rowId: string, patch: RowPatch, optimistic: (row: DatasetRow) => DatasetRow) => {
      const snapshot = rows;
      setRows((prev) => prev.map((r) => (r.id === rowId ? optimistic(r) : r)));
      try {
        const updated = await datasets.patchRow(datasetId, rowId, patch);
        setRows((prev) => prev.map((r) => (r.id === rowId ? updated : r)));
        onChange?.();
      } catch (err) {
        setRows(snapshot);
        setError(err);
      }
    },
    [rows, datasetId, onChange],
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
      applyPatch(row.id, { label: null }, (r) => ({ ...r, label: null, label_source: null }));
    },
    [applyPatch],
  );

  const setNote = useCallback(
    (row: DatasetRow, note: string) => {
      applyPatch(row.id, { note }, (r) => ({ ...r, note }));
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

  function toggleExpanded(key: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function renderCell(row: DatasetRow, column: string) {
    const key = `${row.id}:${column}`;
    const text = scalar(row.data[column]);
    const display = text === null ? "" : String(text);
    const isLong = display.length > CELL_TRUNCATE;
    const isOpen = expanded.has(key);
    if (!isLong) return display;
    return (
      <button type="button" className="cell-expand" onClick={() => toggleExpanded(key)}>
        {isOpen ? display : `${display.slice(0, CELL_TRUNCATE)}…`}
      </button>
    );
  }

  function renderLabelControl(row: DatasetRow) {
    const current = labelValue(row.label);
    if (schema.kind === "categorical") {
      const options = [
        { value: "", label: "—" },
        ...(schema.labels ?? []).map((label) => ({ value: label, label })),
      ];
      return (
        <Select
          options={options}
          aria-label="Label"
          value={current === null ? "" : String(current)}
          onChange={(e) => (e.target.value === "" ? clearLabel(row) : setLabel(row, e.target.value))}
        />
      );
    }
    return (
      <input
        className="select"
        type="number"
        aria-label="Label"
        defaultValue={current === null ? "" : String(current)}
        onBlur={(e) => {
          if (e.target.value === "") clearLabel(row);
          else setLabel(row, Number(e.target.value));
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        }}
      />
    );
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
          {rows.map((row, index) => {
            const suggested = labelValue(row.suggested_label);
            const source = row.label_source;
            return (
              <tr
                key={row.id}
                ref={index === focusedIdx ? focusedRef : null}
                className={index === focusedIdx ? "row-focused" : undefined}
                aria-selected={index === focusedIdx}
                onClick={() => setFocusedIdx(index)}
              >
                {columns.map((column) => (
                  <td key={column}>{renderCell(row, column)}</td>
                ))}
                <td>
                  {suggested === null ? (
                    <span className="muted">—</span>
                  ) : (
                    <div className="suggested-cell">
                      <span>{String(suggested)}</span>
                      {row.label_reasoning && (
                        <button
                          type="button"
                          className="cell-expand"
                          title={row.label_reasoning}
                          onClick={() => toggleExpanded(`${row.id}:reasoning`)}
                        >
                          why?
                        </button>
                      )}
                      {row.label_reasoning && expanded.has(`${row.id}:reasoning`) && (
                        <div className="reasoning">{row.label_reasoning}</div>
                      )}
                      <Button variant="secondary" onClick={() => acceptSuggestion(row)}>
                        Accept
                      </Button>
                    </div>
                  )}
                </td>
                <td>{renderLabelControl(row)}</td>
                <td>{source ? <Badge tone={SOURCE_TONE[source]}>{source}</Badge> : null}</td>
                <td>
                  <input
                    className="select"
                    aria-label="Note"
                    defaultValue={row.note ?? ""}
                    onBlur={(e) => {
                      if (e.target.value !== (row.note ?? "")) setNote(row, e.target.value);
                    }}
                  />
                </td>
              </tr>
            );
          })}
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
