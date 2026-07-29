// Per-field before/after view of a refined evaluator config. Each changed field can be
// accepted (its proposed value applied) or rejected (the current value kept). "Accept all"
// applies every remaining change at once.

import { useEffect, useState } from "react";
import type { GeneratedConfig } from "../api/types";
import { Button } from "./ui";

type ConfigDiffProps = {
  current: GeneratedConfig;
  proposed: GeneratedConfig;
  changedFields: string[];
  onApply: (updates: Partial<GeneratedConfig>) => void;
};

function formatValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "—";
  }
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value, null, 2);
}

export function ConfigDiff({ current, proposed, changedFields, onApply }: ConfigDiffProps) {
  const [pending, setPending] = useState<string[]>(changedFields);

  useEffect(() => {
    setPending(changedFields);
  }, [changedFields]);

  if (pending.length === 0) {
    return null;
  }

  const accept = (field: string) => {
    onApply({ [field]: (proposed as Record<string, unknown>)[field] } as Partial<GeneratedConfig>);
    setPending((rest) => rest.filter((name) => name !== field));
  };

  const reject = (field: string) => {
    setPending((rest) => rest.filter((name) => name !== field));
  };

  const acceptAll = () => {
    const updates: Record<string, unknown> = {};
    for (const field of pending) {
      updates[field] = (proposed as Record<string, unknown>)[field];
    }
    onApply(updates as Partial<GeneratedConfig>);
    setPending([]);
  };

  return (
    <div className="config-diff">
      <div className="config-diff-actions">
        <Button variant="primary" onClick={acceptAll}>
          Accept all
        </Button>
      </div>
      {pending.map((field) => (
        <div key={field} className="config-diff-row">
          <div className="config-diff-field">{field}</div>
          <div className="config-diff-values">
            <div className="config-diff-before">
              <span className="config-diff-label">Before</span>
              <pre>{formatValue((current as Record<string, unknown>)[field])}</pre>
            </div>
            <div className="config-diff-after">
              <span className="config-diff-label">After</span>
              <pre>{formatValue((proposed as Record<string, unknown>)[field])}</pre>
            </div>
          </div>
          <div className="config-diff-buttons">
            <Button variant="secondary" aria-label={`Accept ${field}`} onClick={() => accept(field)}>
              Accept
            </Button>
            <Button variant="secondary" aria-label={`Reject ${field}`} onClick={() => reject(field)}>
              Reject
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}

export default ConfigDiff;
