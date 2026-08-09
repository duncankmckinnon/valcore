// The refine box: a free-text instruction turned into a ConfigDiff via `evaluators.refine`.
// Extracted from VersionEditor; behavior (including the "Refine instruction" aria-label) is
// unchanged. Applying a diff is delegated to the parent through `onApply`.

import { useState } from "react";
import { evaluators } from "../api/client";
import type { GeneratedConfig } from "../api/types";
import { ConfigDiff } from "./ConfigDiff";
import { Button, ErrorBanner, Spinner, TextArea } from "./ui";
import { GATEWAY_BLOCKER, useSetup } from "./useSetup";

type RefinePanelProps = {
  config: GeneratedConfig;
  onApply: (updates: Partial<GeneratedConfig>) => void;
};

export function RefinePanel({ config, onApply }: RefinePanelProps) {
  const [instruction, setInstruction] = useState("");
  const [refining, setRefining] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [diff, setDiff] = useState<{ config: GeneratedConfig; changed: string[] } | null>(null);
  const { gatewayReady } = useSetup();

  const handleRefine = async () => {
    if (instruction.trim() === "") {
      return;
    }
    setRefining(true);
    setError(null);
    try {
      const refined = await evaluators.refine({ config, instruction });
      setDiff({ config: refined.config, changed: refined.changed_fields });
    } catch (err) {
      setError(err);
    } finally {
      setRefining(false);
    }
  };

  return (
    <div className="refine-box">
      <span className="field-label">Refine</span>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <TextArea
        aria-label="Refine instruction"
        placeholder="Describe a change in plain language…"
        value={instruction}
        onChange={(event) => setInstruction(event.target.value)}
      />
      {!gatewayReady && <p className="form-footer-blocker">{GATEWAY_BLOCKER}</p>}
      <Button variant="secondary" onClick={handleRefine} disabled={refining || !gatewayReady}>
        {refining ? <Spinner /> : "Refine"}
      </Button>
      {diff && (
        <ConfigDiff
          current={config}
          proposed={diff.config}
          changedFields={diff.changed}
          onApply={onApply}
        />
      )}
    </div>
  );
}

export default RefinePanel;
