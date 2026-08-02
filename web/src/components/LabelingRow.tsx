// One labeling-table row: the editable data cells, the suggested-label cell with
// its "why?" expander and Accept button, the label control, the source badge, the
// note input, and the delete action. The grid owns fetching, pagination, the
// keyboard handler, and the shared optimistic-update path; this file is only the row.

import { forwardRef } from "react";
import type { CSSProperties } from "react";
import type { DatasetRow, LabelSchema, LabelSource } from "../api/types";
import { Badge, Button, Select } from "./ui";

export type LabelingRowProps = {
  row: DatasetRow;
  columns: string[];
  schema: LabelSchema;
  focused: boolean;
  expanded: Set<string>;
  onToggleExpanded: (key: string) => void;
  onFocus: () => void;
  onSetLabel: (value: string | number) => void;
  onClearLabel: () => void;
  onAcceptSuggestion: () => void;
  onSetNote: (note: string) => void;
  onSetCell: (column: string, value: string) => void;
  onDelete: () => void;
};

const CELL_TRUNCATE = 80;

const SOURCE_TONE: Record<LabelSource, "neutral" | "success" | "warning"> = {
  manual: "neutral",
  accepted: "success",
  generated: "warning",
};

// The editable input carries the cell's value, but an input's value is not part of
// its text content, so the value is also mirrored in a visually-hidden node. That
// keeps the cell discoverable by text queries without a second visible control.
const VALUE_MIRROR: CSSProperties = {
  position: "absolute",
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
  border: 0,
};

function scalar(value: unknown): string | number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string" || typeof value === "number") return value;
  return String(value);
}

function labelValue(label: Record<string, unknown> | null): string | number | null {
  return label ? scalar(label.value) : null;
}

const LabelingRow = forwardRef<HTMLTableRowElement, LabelingRowProps>(function LabelingRow(
  {
    row,
    columns,
    schema,
    focused,
    expanded,
    onToggleExpanded,
    onFocus,
    onSetLabel,
    onClearLabel,
    onAcceptSuggestion,
    onSetNote,
    onSetCell,
    onDelete,
  },
  ref,
) {
  function renderCell(column: string) {
    const key = `${row.id}:${column}`;
    const text = scalar(row.data[column]);
    const display = text === null ? "" : String(text);
    const isLong = display.length > CELL_TRUNCATE;

    if (isLong) {
      if (expanded.has(key)) {
        // Keyed on the value so an optimistic rollback remounts the editor with the
        // reverted text rather than leaving the failed edit on screen.
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
      <>
        {display !== "" && <span style={VALUE_MIRROR}>{display}</span>}
        <input
          key={display}
          className="cell-input"
          aria-label={column}
          defaultValue={display}
          onBlur={(e) => {
            if (e.target.value !== display) onSetCell(column, e.target.value);
          }}
        />
      </>
    );
  }

  function renderLabelControl() {
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
          onChange={(e) =>
            e.target.value === "" ? onClearLabel() : onSetLabel(e.target.value)
          }
        />
      );
    }
    // Keyed on the current value so an optimistic rollback remounts the input with
    // the reverted value rather than leaving the failed edit on screen.
    return (
      <input
        key={current === null ? "" : String(current)}
        className="select"
        type="number"
        aria-label="Label"
        defaultValue={current === null ? "" : String(current)}
        onBlur={(e) => {
          if (e.target.value === "") onClearLabel();
          else onSetLabel(Number(e.target.value));
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        }}
      />
    );
  }

  const suggested = labelValue(row.suggested_label);
  const source = row.label_source;

  return (
    <tr
      ref={ref}
      data-row-id={row.id}
      className={focused ? "row-focused" : undefined}
      aria-selected={focused}
      onClick={onFocus}
    >
      {columns.map((column) => (
        <td key={column}>{renderCell(column)}</td>
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
                onClick={() => onToggleExpanded(`${row.id}:reasoning`)}
              >
                why?
              </button>
            )}
            {row.label_reasoning && expanded.has(`${row.id}:reasoning`) && (
              <div className="reasoning">{row.label_reasoning}</div>
            )}
            <Button variant="secondary" onClick={onAcceptSuggestion}>
              Accept
            </Button>
          </div>
        )}
      </td>
      <td>{renderLabelControl()}</td>
      <td>{source ? <Badge tone={SOURCE_TONE[source]}>{source}</Badge> : null}</td>
      <td>
        <div className="row-actions">
          <input
            key={row.note ?? ""}
            className="select"
            aria-label="Note"
            defaultValue={row.note ?? ""}
            onBlur={(e) => {
              if (e.target.value !== (row.note ?? "")) onSetNote(e.target.value);
            }}
          />
          <button
            type="button"
            className="row-delete"
            aria-label={`Delete row ${row.idx}`}
            onClick={onDelete}
          >
            ×
          </button>
        </div>
      </td>
    </tr>
  );
});

export default LabelingRow;
