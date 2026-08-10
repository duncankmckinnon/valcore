// Per-column notes editor shared by both seeded-generation modals. The payload shape is
// identical whether we derive a dataset from an evaluator or an evaluator from a dataset,
// so the component is parameterized by its prompt text (`notePlaceholder`) rather than
// duplicated per direction. It is fully controlled: the parent owns the notes record and
// the extra-column list, and this component only reports edits back.

import { useState } from "react";
import { Button } from "./ui";

type ColumnNotesEditorProps = {
  lockedColumns: string[]; // rendered read-only, cannot be removed
  extraColumns: string[]; // rendered removable
  notes: Record<string, string>;
  notePlaceholder: string; // the per-direction question
  allowAddColumns: boolean;
  // Why a locked column is locked, e.g. "required". Omit where the columns are the user's
  // own and nothing upstream fixes them, so no badge is rendered rather than a wrong one.
  lockedBadge?: string;
  // Opt-in selection: when both are supplied, each locked column gets an include checkbox and
  // the caller sends only the checked ones. Omit both where every column always applies, so no
  // checkbox is rendered rather than a decorative one that controls nothing.
  selectedColumns?: string[];
  onChangeSelected?: (columns: string[]) => void;
  onChangeNotes: (notes: Record<string, string>) => void;
  onChangeExtraColumns: (columns: string[]) => void;
};

export function ColumnNotesEditor({
  lockedColumns,
  extraColumns,
  notes,
  notePlaceholder,
  allowAddColumns,
  lockedBadge,
  selectedColumns,
  onChangeSelected,
  onChangeNotes,
  onChangeExtraColumns,
}: ColumnNotesEditorProps) {
  const [draft, setDraft] = useState("");

  const selectable = selectedColumns !== undefined && onChangeSelected !== undefined;
  const isSelected = (column: string) => !selectable || selectedColumns.includes(column);

  const toggle = (column: string) => {
    if (!selectable) return;
    onChangeSelected(
      selectedColumns.includes(column)
        ? selectedColumns.filter((c) => c !== column)
        : // Re-add in `lockedColumns` order so the payload never depends on click order.
          lockedColumns.filter((c) => c === column || selectedColumns.includes(c)),
    );
  };

  const setNote = (column: string, note: string) => {
    onChangeNotes({ ...notes, [column]: note });
  };

  const removeExtra = (column: string) => {
    onChangeExtraColumns(extraColumns.filter((c) => c !== column));
    // The note travels with the column, so drop it too rather than leave it orphaned.
    const { [column]: _dropped, ...rest } = notes;
    onChangeNotes(rest);
  };

  const addColumn = () => {
    const name = draft.trim();
    if (!name) return;
    // The model never invents columns and `_valid_rows` compares key sets by exact
    // equality, so a duplicate would either be redundant or silently drop every row.
    if (lockedColumns.includes(name) || extraColumns.includes(name)) return;
    onChangeExtraColumns([...extraColumns, name]);
    setDraft("");
  };

  return (
    <div className="column-notes-editor">
      {lockedColumns.map((column) => (
        <div key={column} className="column-note-row column-note-locked">
          {selectable && (
            <input
              type="checkbox"
              aria-label={`Include ${column}`}
              checked={isSelected(column)}
              onChange={() => toggle(column)}
            />
          )}
          <span className="column-note-name">{column}</span>
          {lockedBadge && isSelected(column) && (
            <span className="badge" aria-hidden="true">
              {lockedBadge}
            </span>
          )}
          <input
            className="input"
            aria-label={`Note for ${column}`}
            placeholder={notePlaceholder}
            value={notes[column] ?? ""}
            onChange={(event) => setNote(column, event.target.value)}
            // A note on an excluded column would be sent nowhere, so disable rather than
            // collect it. `disabled` and not `pointer-events`, which leaves keyboard entry open.
            disabled={!isSelected(column)}
          />
        </div>
      ))}

      {extraColumns.map((column) => (
        <div key={column} className="column-note-row">
          <span className="column-note-name">{column}</span>
          <input
            className="input"
            aria-label={`Note for ${column}`}
            placeholder={notePlaceholder}
            value={notes[column] ?? ""}
            onChange={(event) => setNote(column, event.target.value)}
          />
          <Button
            variant="danger"
            aria-label={`Remove ${column}`}
            onClick={() => removeExtra(column)}
          >
            ×
          </Button>
        </div>
      ))}

      {allowAddColumns && (
        <div className="column-note-add">
          <input
            className="input"
            aria-label="New column name"
            placeholder="Add a column"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                addColumn();
              }
            }}
          />
          <Button variant="secondary" onClick={addColumn}>
            Add column
          </Button>
        </div>
      )}
    </div>
  );
}

export default ColumnNotesEditor;
