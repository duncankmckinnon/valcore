// A two-level export picker shared by both evaluators and datasets. The outer choice is a
// format (Code / JSON); JSON additionally offers a bundled-or-split layout. Code for an
// evaluator keeps today's single-source-block behaviour byte-for-byte — one unnamed <pre> with
// a Copy button in the footer — because that path still emits the standalone Python script the
// modal has always emitted. Every other combination is a file package: one named block per
// emitted file, each with its own Copy and Download.

import { useEffect, useState } from "react";
import { datasets, evaluators } from "../api/client";
import type { ExportFormat, ExportLayout } from "../api/types";
import { Button, ErrorBanner, Modal, Select, Spinner } from "./ui";

type ExportSubject =
  | { kind: "evaluator"; evaluatorId: string; versionId: string }
  | { kind: "dataset"; datasetId: string; versionId?: string };

type ExportModalProps = {
  open: boolean;
  onClose: () => void;
  subject: ExportSubject;
};

// The fetched payload is either the single Python script (evaluator Code) or a filename-keyed
// map. Discriminating here keeps the render branch from having to guess which shape it holds.
type Fetched =
  | { kind: "script"; text: string }
  | { kind: "files"; files: Record<string, string> };

// JSON bodies are shown pretty-printed, but the same block also carries Python source (dataset
// Code) and could carry a malformed body; a failed parse falls back to the raw text rather than
// throwing, so a non-JSON body renders verbatim.
function forDisplay(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

export function ExportModal({ open, onClose, subject }: ExportModalProps) {
  const [format, setFormat] = useState<ExportFormat>("code");
  const [layout, setLayout] = useState<ExportLayout>("bundled");
  const [result, setResult] = useState<Fetched | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  // Which block last had its text copied, keyed by filename ("" for the unnamed script block).
  // Null means nothing copied since the current fetch began.
  const [copied, setCopied] = useState<string | null>(null);

  // A string identity for the subject so the fetch effect refires when the subject changes but
  // not on every unrelated parent re-render that hands us a fresh object literal.
  const subjectKey =
    subject.kind === "evaluator"
      ? `evaluator:${subject.evaluatorId}:${subject.versionId}`
      : `dataset:${subject.datasetId}:${subject.versionId ?? ""}`;

  useEffect(() => {
    if (!open) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setCopied(null);

    const run = async (): Promise<Fetched> => {
      // Evaluator Code is the one path that stays the legacy standalone script.
      if (subject.kind === "evaluator" && format === "code") {
        const text = await evaluators.exportScript(subject.evaluatorId, subject.versionId);
        return { kind: "script", text };
      }
      if (subject.kind === "evaluator") {
        const files = await evaluators.exportFiles(subject.versionId, format, layout);
        return { kind: "files", files };
      }
      const files = await datasets.exportFiles(subject.datasetId, format, {
        versionId: subject.versionId,
        layout,
      });
      return { kind: "files", files };
    };

    run()
      .then((fetched) => {
        // Guard against a stale in-flight response overwriting a newer one: a superseded fetch
        // has already had `cancelled` flipped by its cleanup before this resolves.
        if (!cancelled) {
          setResult(fetched);
        }
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
    // subjectKey stands in for the subject fields read inside `run`.
  }, [open, subjectKey, format, layout]);

  const copy = async (key: string, text: string) => {
    await navigator.clipboard.writeText(text);
    setCopied(key);
  };

  // One file per Blob, named by its map key; the object URL is revoked once the synthesized
  // anchor has fired so it does not leak.
  const download = (name: string, text: string) => {
    const url = URL.createObjectURL(new Blob([text], { type: "application/octet-stream" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const isScript = subject.kind === "evaluator" && format === "code";

  const description =
    format === "json"
      ? "A pydantic-evals dataset paired with a pydantic-ai agent spec. Keep valcore_judge.py " +
        "beside the package to run it."
      : subject.kind === "evaluator"
        ? "A standalone Python script that runs this evaluator on its own, without valcore installed."
        : "A Python module that builds a pydantic_evals.Dataset from this dataset.";

  return (
    <Modal
      open={open}
      title={`Export ${subject.kind}`}
      description={description}
      size="lg"
      onClose={onClose}
      footer={
        <div className="export-actions">
          {/* The unnamed script block copies from the footer, preserving today's layout. File
              packages copy per file, so the footer carries no Copy for them. Copy is withheld
              until the fetch resolves, so there is never a live copy action with nothing to
              write. */}
          {!loading && isScript && result?.kind === "script" && (
            <Button
              variant="secondary"
              onClick={() => copy("", result.text)}
              disabled={!result.text}
            >
              {copied !== null ? "Copied" : "Copy"}
            </Button>
          )}
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        </div>
      }
    >
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <fieldset className="export-format">
        <legend>Format</legend>
        <label>
          <input
            type="radio"
            name="export-format"
            checked={format === "code"}
            onChange={() => setFormat("code")}
          />{" "}
          Code
        </label>
        <label>
          <input
            type="radio"
            name="export-format"
            checked={format === "json"}
            onChange={() => setFormat("json")}
          />{" "}
          JSON
        </label>
      </fieldset>

      {/* The layout choice only exists for the JSON package; Code has a single form. */}
      {format === "json" && (
        <div className="export-layout">
          <Select
            aria-label="Layout"
            value={layout}
            onChange={(event) => setLayout(event.target.value as ExportLayout)}
            options={[
              { value: "bundled", label: "Bundled" },
              { value: "split", label: "Split" },
            ]}
          />
        </div>
      )}

      {loading ? (
        <Spinner />
      ) : result?.kind === "script" ? (
        <pre className="export-source">{result.text}</pre>
      ) : result?.kind === "files" ? (
        Object.entries(result.files).map(([name, body]) => (
          <div className="export-file" key={name}>
            <div className="export-file-name">{name}</div>
            <pre className="export-source">{forDisplay(body)}</pre>
            <div className="export-file-actions">
              <Button variant="secondary" onClick={() => copy(name, body)}>
                {copied === name ? "Copied" : "Copy"}
              </Button>
              <Button variant="secondary" onClick={() => download(name, body)}>
                Download
              </Button>
            </div>
          </div>
        ))
      ) : null}
    </Modal>
  );
}

export default ExportModal;
