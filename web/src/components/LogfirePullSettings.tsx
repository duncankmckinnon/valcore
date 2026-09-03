// Read-only provenance for a dataset pulled from Logfire. Renders nothing when the
// dataset was not pulled, matching GenerationSettings' silence for uploaded rows.

import type { DatasetLogfirePull } from "../api/types";

type Props = { pull: DatasetLogfirePull | null };

export function LogfirePullSettings({ pull }: Props) {
  if (pull === null) return null;

  return (
    <details className="generation-settings">
      <summary>How these rows were pulled from Logfire</summary>
      <div className="generation-field">
        <span className="field-label">SQL</span>
        <pre className="generation-text">{pull.sql}</pre>
      </div>
      <div className="generation-field">
        <span className="field-label">Sample size</span>
        <span>{pull.sample_n}</span>
      </div>
      <div className="generation-field">
        <span className="field-label">Seed</span>
        <span>{pull.seed}</span>
      </div>
      {pull.min_timestamp && (
        <div className="generation-field">
          <span className="field-label">From</span>
          <span>{pull.min_timestamp}</span>
        </div>
      )}
      {pull.max_timestamp && (
        <div className="generation-field">
          <span className="field-label">Until</span>
          <span>{pull.max_timestamp}</span>
        </div>
      )}
      {pull.label_column && (
        <div className="generation-field">
          <span className="field-label">Label column</span>
          <span>{pull.label_column}</span>
        </div>
      )}
    </details>
  );
}
