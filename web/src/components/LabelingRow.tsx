// One annotation-queue row: the editable data cells, the label control (multi-select
// chips for categorical, a number input for numeric), the suggested-value cell with its
// "why?" expander and Accept button, the source badge, the description field, and the
// row actions (open full page, clear annotation, delete row). The grid owns fetching,
// pagination, the keyboard handler, and the shared optimistic-update path; this file is
// only the row.

import { forwardRef } from "react";
import type { AnnotationLabel, AnnotationRow, LabelSetProgress, LabelSource } from "../api/types";
import { Badge, Button } from "./ui";

export type LabelingRowProps = {
  row: AnnotationRow;
  columns: string[];
  labelSet: LabelSetProgress;
  focused: boolean;
  expanded: Set<string>;
  onToggleExpanded: (key: string) => void;
  onFocus: () => void;
  onToggleLabel: (name: string) => void;
  onSetValue: (value: number) => void;
  onClearAnnotation: () => void;
  onAcceptSuggestion: () => void;
  onSetDescription: (description: string) => void;
  onSetCell: (column: string, value: string) => void;
  onOpen: () => void;
  onDelete: () => void;
};

const CELL_TRUNCATE = 80;

const SOURCE_TONE: Record<LabelSource, "neutral" | "success" | "warning"> = {
  manual: "neutral",
  accepted: "success",
  generated: "warning",
};

function scalar(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value, null, 2);
}

const LabelingRow = forwardRef<HTMLTableRowElement, LabelingRowProps>(function LabelingRow(
  {
    row,
    columns,
    labelSet,
    focused,
    expanded,
    onToggleExpanded,
    onFocus,
    onToggleLabel,
    onSetValue,
    onClearAnnotation,
    onAcceptSuggestion,
    onSetDescription,
    onSetCell,
    onOpen,
    onDelete,
  },
  ref,
) {
  function renderCell(column: string) {
    const key = `${row.row_id}:${column}`;
    const display = scalar(row.data[column]);
    const isLong = display.length > CELL_TRUNCATE;

    if (isLong) {
      if (expanded.has(key)) {
        return (
          <textarea
            key={display}
            className="cell-textarea"
            aria-label={column}
            defaultValue={display}
            onBlur={(e) => {
              if (e.target.value !== display) onSetCell(column, e.target.value);
            }}
          />
        );
      }
      return (
        <button type="button" className="cell-expand" onClick={() => onToggleExpanded(key)}>
          {`${display.slice(0, CELL_TRUNCATE)}…`}
        </button>
      );
    }

    return (
      <input
        key={display}
        className="cell-input"
        aria-label={column}
        defaultValue={display}
        onBlur={(e) => {
          if (e.target.value !== display) onSetCell(column, e.target.value);
        }}
      />
    );
  }

  function renderLabelsControl() {
    if (labelSet.kind === "categorical") {
      const selected = row.annotation?.labels ?? [];
      const options: AnnotationLabel[] = labelSet.labels ?? [];
      return (
        <div className="chips" role="group" aria-label="Labels">
          {options.map((label) => (
            <button
              key={label.name}
              type="button"
              className={`chip ${selected.includes(label.name) ? "chip-selected" : ""}`.trim()}
              aria-pressed={selected.includes(label.name)}
              title={label.description || undefined}
              onClick={() => onToggleLabel(label.name)}
            >
              {label.name}
            </button>
          ))}
        </div>
      );
    }
    const current = row.annotation?.value;
    return (
      <input
        key={current ?? ""}
        className="select"
        type="number"
        aria-label="Value"
        defaultValue={current ?? ""}
        onBlur={(e) => {
          if (e.target.value !== "") onSetValue(Number(e.target.value));
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        }}
      />
    );
  }

  const annotation = row.annotation;
  const hasSuggestion =
    labelSet.kind === "categorical"
      ? (annotation?.suggested_labels?.length ?? 0) > 0
      : annotation?.suggested_value !== null && annotation?.suggested_value !== undefined;
  const source = annotation?.source ?? null;

  return (
    <tr
      ref={ref}
      data-row-id={row.row_id}
      className={focused ? "row-focused" : undefined}
      aria-selected={focused}
      onClick={onFocus}
    >
      {columns.map((column) => (
        <td key={column}>{renderCell(column)}</td>
      ))}
      <td>{renderLabelsControl()}</td>
      <td>
        {!hasSuggestion ? (
          <span className="muted">—</span>
        ) : (
          <div className="suggested-cell">
            <span>
              {labelSet.kind === "categorical"
                ? (annotation?.suggested_labels ?? []).join(", ")
                : String(annotation?.suggested_value)}
            </span>
            {annotation?.reasoning && (
              <button
                type="button"
                className="cell-expand"
                title={annotation.reasoning}
                onClick={() => onToggleExpanded(`${row.row_id}:reasoning`)}
              >
                why?
              </button>
            )}
            {annotation?.reasoning && expanded.has(`${row.row_id}:reasoning`) && (
              <div className="reasoning">{annotation.reasoning}</div>
            )}
            <Button variant="secondary" onClick={onAcceptSuggestion}>
              Accept
            </Button>
          </div>
        )}
      </td>
      <td>{source ? <Badge tone={SOURCE_TONE[source]}>{source}</Badge> : null}</td>
      <td>
        <input
          key={annotation?.description ?? ""}
          className="select"
          aria-label="Description"
          defaultValue={annotation?.description ?? ""}
          onBlur={(e) => {
            if (e.target.value !== (annotation?.description ?? "")) {
              onSetDescription(e.target.value);
            }
          }}
        />
      </td>
      <td>
        <div className="row-actions">
          <button
            type="button"
            className="cell-expand"
            aria-label={`Open row ${row.idx}`}
            onClick={onOpen}
          >
            Open
          </button>
          {annotation !== null && (
            <button
              type="button"
              className="row-delete"
              aria-label={`Clear annotation for row ${row.idx}`}
              onClick={onClearAnnotation}
            >
              ×
            </button>
          )}
          <button
            type="button"
            className="row-delete"
            aria-label={`Delete row ${row.idx}`}
            onClick={onDelete}
          >
            ⌫
          </button>
        </div>
      </td>
    </tr>
  );
});

export default LabelingRow;
