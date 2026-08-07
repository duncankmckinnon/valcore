// Fetches the standalone Python script for a version and shows it in a <pre> with a
// copy-to-clipboard button.

import { useEffect, useState } from "react";
import { evaluators } from "../api/client";
import { Button, ErrorBanner, Modal, Spinner } from "./ui";

type ExportModalProps = {
  open: boolean;
  evaluatorId: string;
  versionId: string;
  onClose: () => void;
};

export function ExportModal({ open, evaluatorId, versionId, onClose }: ExportModalProps) {
  const [source, setSource] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!open) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setCopied(false);
    evaluators
      .exportScript(evaluatorId, versionId)
      .then((result) => {
        if (cancelled) {
          return;
        }
        const text =
          typeof result === "string"
            ? result
            : ((result as { source?: string }).source ?? JSON.stringify(result, null, 2));
        setSource(text);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open, evaluatorId, versionId]);

  const copy = async () => {
    await navigator.clipboard.writeText(source);
    setCopied(true);
  };

  return (
    <Modal
      open={open}
      title="Export evaluator"
      description="A standalone Python script that runs this evaluator on its own, without valcore installed."
      size="lg"
      onClose={onClose}
      footer={
        <div className="export-actions">
          {/* Copy is withheld until the script has loaded, so there is never a live copy
              action with nothing to write. */}
          {!loading && (
            <Button variant="secondary" onClick={copy} disabled={!source}>
              {copied ? "Copied" : "Copy"}
            </Button>
          )}
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        </div>
      }
    >
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {loading ? <Spinner /> : <pre className="export-source">{source}</pre>}
    </Modal>
  );
}

export default ExportModal;
