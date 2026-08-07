// Settings editor: rename a dataset, reshape its columns, and adjust its label space.
// Column moves are diffed into a rename map; the server owns whether the reshape is
// destructive, so a refusal is surfaced with its exact label-loss count.

import { useState } from "react";
import { ApiError, datasets } from "../api/client";
import type { Dataset, DatasetUpdate, LabelSchema } from "../api/types";
import { Button, ErrorBanner, Modal, Spinner } from "./ui";
import LabelSchemaEditor from "./LabelSchemaEditor";

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
  const [schema, setSchema] = useState<LabelSchema>(dataset.label_schema as LabelSchema);
  const [rows, setRows] = useState<ColumnRow[]>(
    dataset.columns.map((column) => ({ original: column, current: column })),
  );
  const [duplicateError, setDuplicateError] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const [pending, setPending] = useState<{ body: DatasetUpdate; count: number } | null>(null);

  const schemaChanged = JSON.stringify(schema) !== JSON.stringify(dataset.label_schema);

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

  // Derive the PATCH body from the current form, or a duplicate-name complaint.
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
    if (schemaChanged) body.label_schema = schema;
    return { body };
  }

  async function send(body: DatasetUpdate, force: boolean) {
    setSubmitting(true);
    setError(null);
    try {
      const saved = await datasets.update(dataset.id, force ? { ...body, force: true } : body);
      onSaved(saved);
      onClose();
    } catch (err) {
      if (err instanceof ApiError && err.type === "DestructiveChangeError") {
        setPending({ body, count: Number(err.detail?.invalid_label_count ?? 0) });
      } else {
        setError(err);
      }
    } finally {
      setSubmitting(false);
    }
  }

  async function onSave() {
    setDuplicateError(null);
    setPending(null);
    const built = buildBody();
    if ("duplicate" in built) {
      setDuplicateError(built.duplicate);
      return;
    }
    await send(built.body, false);
  }

  const footer = pending ? (
    <div className="modal-actions">
      <Button variant="secondary" onClick={() => setPending(null)} disabled={submitting}>
        Cancel
      </Button>
      <Button onClick={() => send(pending.body, true)} disabled={submitting}>
        {submitting ? <Spinner /> : "Save anyway"}
      </Button>
    </div>
  ) : (
    <div className="modal-actions">
      <Button variant="secondary" onClick={onClose} disabled={submitting}>
        Cancel
      </Button>
      <Button onClick={onSave} disabled={submitting}>
        {submitting ? <Spinner /> : "Save"}
      </Button>
    </div>
  );

  return (
    <Modal
      open={open}
      title="Dataset settings"
      description="The stored generation settings for this dataset. Editing them shapes future generation, not the existing rows."
      onClose={onClose}
      footer={footer}
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

        <LabelSchemaEditor value={schema} onChange={setSchema} />

        {duplicateError && <p className="destructive-warning">{duplicateError}</p>}

        {schemaChanged && !pending && (
          <p className="destructive-warning">
            Changing the label space may clear labels that no longer fit.
          </p>
        )}

        {pending && (
          <p className="destructive-warning">{pending.count} rows will lose their label.</p>
        )}
      </div>
    </Modal>
  );
}
