// Settings editor: rename a dataset and reshape its columns. A dataset's label space is
// no longer part of its shape -- it lives in label sets, managed from the Annotations
// page -- so this modal only ever sends name/description/columns/column_renames.

import { useState } from "react";
import { datasets } from "../api/client";
import type { Dataset, DatasetUpdate } from "../api/types";
import { Button, ErrorBanner, Modal, Spinner } from "./ui";

type DatasetSettingsModalProps = {
  open: boolean;
  dataset: Dataset;
  onSaved: (dataset: Dataset) => void;
  onClose: () => void;
};

// Each editor row remembers its persisted name (null once it is a fresh row) so a
// move can be told apart from a rename when the patch is derived.
type ColumnRow = { original: string | null; current: string };

export default function DatasetSettingsModal({
  open,
  dataset,
  onSaved,
  onClose,
}: DatasetSettingsModalProps) {
  const [name, setName] = useState(dataset.name);
  const [description, setDescription] = useState(dataset.description);
  const [rows, setRows] = useState<ColumnRow[]>(
    dataset.columns.map((column) => ({ original: column, current: column })),
  );
  const [duplicateError, setDuplicateError] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  function setRowName(index: number, value: string) {
    setRows((current) =>
      current.map((row, i) => (i === index ? { ...row, current: value } : row)),
    );
  }

  function addRow() {
    setRows((current) => [...current, { original: null, current: "" }]);
  }

  function removeRow(index: number) {
    setRows((current) => current.filter((_, i) => i !== index));
  }

  function buildBody(): { body: DatasetUpdate } | { duplicate: string } {
    const columns = rows.map((row) => row.current.trim()).filter(Boolean);
    if (new Set(columns).size !== columns.length) {
      return { duplicate: "Column names must be unique — resolve the duplicate before saving." };
    }

    const renames: Record<string, string> = {};
    for (const row of rows) {
      const current = row.current.trim();
      if (row.original !== null && current !== "" && current !== row.original) {
        renames[row.original] = current;
      }
    }

    const body: DatasetUpdate = {};
    if (name.trim() !== dataset.name) body.name = name.trim();
    if (description.trim() !== dataset.description) body.description = description.trim();
    if (JSON.stringify(columns) !== JSON.stringify(dataset.columns)) body.columns = columns;
    if (Object.keys(renames).length > 0) body.column_renames = renames;
    return { body };
  }

  async function onSave() {
    setDuplicateError(null);
    const built = buildBody();
    if ("duplicate" in built) {
      setDuplicateError(built.duplicate);
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const saved = await datasets.update(dataset.id, built.body);
      onSaved(saved);
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={open}
      title="Dataset settings"
      description="Rename this dataset or reshape its columns."
      onClose={onClose}
      footer={
        <div className="modal-actions">
          <Button variant="secondary" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={onSave} disabled={submitting}>
            {submitting ? <Spinner /> : "Save"}
          </Button>
        </div>
      }
    >
      <div className="dataset-settings">
        <ErrorBanner error={error} onDismiss={() => setError(null)} />

        <label className="field">
          <span className="field-label">Name</span>
          <input className="select" value={name} onChange={(e) => setName(e.target.value)} />
        </label>

        <label className="field">
          <span className="field-label">Description</span>
          <textarea
            className="textarea"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>

        <div className="field">
          <span className="field-label">Columns</span>
          <div className="column-editor">
            {rows.map((row, index) => (
              <div className="column-editor-row" key={index}>
                <input
                  className="select"
                  aria-label={`Column ${index + 1}`}
                  value={row.current}
                  onChange={(e) => setRowName(index, e.target.value)}
                />
                <Button
                  type="button"
                  variant="secondary"
                  aria-label={`Remove column ${index + 1}`}
                  onClick={() => removeRow(index)}
                >
                  Remove
                </Button>
              </div>
            ))}
          </div>
          <Button type="button" variant="secondary" onClick={addRow}>
            Add column
          </Button>
        </div>

        {duplicateError && <p className="destructive-warning">{duplicateError}</p>}
      </div>
    </Modal>
  );
}
