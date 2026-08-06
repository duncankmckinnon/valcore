// Read-only provenance for a generated dataset: what was asked for to produce these rows.
// Renders nothing for a dataset that was uploaded or created blank, which has no settings —
// absence is the normal case, not an error worth reporting.

import type { DatasetGeneration } from "../api/types";

type GenerationSettingsProps = { generation: DatasetGeneration | null };

/** Percent-format a mix share for display, dropping a trailing ".0". */
function asPercent(share: number): string {
  const percent = share * 100;
  return `${Number.isInteger(percent) ? percent : percent.toFixed(1)}%`;
}

export function GenerationSettings({ generation }: GenerationSettingsProps) {
  if (generation === null) return null;

  const notes = Object.entries(generation.column_notes ?? {});
  const mix = Object.entries(generation.label_mix ?? {});

  return (
    <details className="generation-settings">
      <summary>How these rows were generated</summary>

      <div className="generation-field">
        <span className="field-label">Rows requested</span>
        <span>{generation.count}</span>
      </div>

      {generation.instructions && (
        <div className="generation-field">
          <span className="field-label">Instructions</span>
          <p className="generation-text">{generation.instructions}</p>
        </div>
      )}

      {generation.label_guidance && (
        <div className="generation-field">
          <span className="field-label">Label guidance</span>
          <p className="generation-text">{generation.label_guidance}</p>
        </div>
      )}

      {notes.length > 0 && (
        <div className="generation-field">
          <span className="field-label">Column notes</span>
          <ul className="generation-list">
            {notes.map(([column, note]) => (
              <li key={column}>
                <strong>{column}</strong>: {note}
              </li>
            ))}
          </ul>
        </div>
      )}

      {mix.length > 0 && (
        <div className="generation-field">
          <span className="field-label">Label distribution</span>
          <div className="label-chips">
            {mix.map(([label, share]) => (
              <span key={label} className="badge">
                {label}: {asPercent(share)}
              </span>
            ))}
          </div>
        </div>
      )}

      {generation.source_version_id && (
        <div className="generation-field">
          <span className="field-label">Seeded from evaluator version</span>
          {/* Provenance only: the version may since have changed or been deleted, so this
              is shown as an id rather than a link that could 404. */}
          <span className="generation-id">{generation.source_version_id}</span>
        </div>
      )}
    </details>
  );
}

export default GenerationSettings;
