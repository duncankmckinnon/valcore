// Detail view for a single evaluator: an editable title, a version selector across the top,
// the VersionEditor below, and the Export / Run / delete actions. A "New version" button and
// the zero-version state both drop into a client-side draft, so an evaluator created from
// scratch is immediately usable without generating anything.

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, evaluators } from "../api/client";
import type { EvaluatorVersion } from "../api/types";
import DatasetFromEvaluator from "../components/DatasetFromEvaluator";
import { ExportModal } from "../components/ExportModal";
import type { AppConfig } from "../components/VersionEditor";
import { VersionEditor } from "../components/VersionEditor";
import { Badge, Button, ConfirmDialog, ErrorBanner, Select, Spinner } from "../components/ui";
import { PageHeader } from "../components/PageHeader";

type EvaluatorDetailData = {
  id: string;
  name: string;
  description: string;
  active_version_id: string | null;
  versions: EvaluatorVersion[];
};

type EvaluatorDetailProps = {
  id: string;
};

export default function EvaluatorDetail({ id }: EvaluatorDetailProps) {
  const navigate = useNavigate();
  const [detail, setDetail] = useState<EvaluatorDetailData | null>(null);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // The draft is a client-side sentinel rather than a magic version id, so it can never
  // collide with a real one (see plan: unsaveable empty version).
  const [draft, setDraft] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [generating, setGenerating] = useState(false);

  const [editingTitle, setEditingTitle] = useState(false);
  const [titleValue, setTitleValue] = useState("");
  const [editingDescription, setEditingDescription] = useState(false);
  const [descriptionValue, setDescriptionValue] = useState("");

  const [deleteVersionOpen, setDeleteVersionOpen] = useState(false);
  const [deleteVersionBusy, setDeleteVersionBusy] = useState(false);
  const [deleteVersionError, setDeleteVersionError] = useState<unknown>(null);
  const [deleteEvaluatorOpen, setDeleteEvaluatorOpen] = useState(false);
  const [deleteEvaluatorBusy, setDeleteEvaluatorBusy] = useState(false);
  const [deleteEvaluatorError, setDeleteEvaluatorError] = useState<unknown>(null);

  const load = useCallback(
    async (preferVersionId?: string) => {
      setLoading(true);
      try {
        const data = (await evaluators.get(id)) as unknown as EvaluatorDetailData;
        setDetail(data);
        setSelectedId((current) => {
          const preferred = preferVersionId ?? current ?? data.active_version_id;
          if (preferred && data.versions.some((version) => version.id === preferred)) {
            return preferred;
          }
          // When the preferred selection is gone (e.g. it was just deleted), fall to the
          // newest survivor rather than whichever the API happens to list first.
          const newest = data.versions.reduce<EvaluatorVersion | null>(
            (latest, version) =>
              !latest || version.created_at > latest.created_at ? version : latest,
            null,
          );
          return newest?.id ?? null;
        });
      } catch (err) {
        setError(err);
      } finally {
        setLoading(false);
      }
    },
    [id],
  );

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    api<AppConfig>("/api/config").then(setConfig).catch(setError);
  }, []);

  if (loading && !detail) {
    return <Spinner />;
  }

  if (!detail || !config) {
    return <ErrorBanner error={error} onDismiss={() => setError(null)} />;
  }

  const selected = detail.versions.find((version) => version.id === selectedId) ?? null;
  // An evaluator with no versions has nowhere else to go, so it opens straight into the draft.
  const showDraft = draft || detail.versions.length === 0;

  const commitTitle = async () => {
    setEditingTitle(false);
    const next = titleValue.trim();
    if (next === "" || next === detail.name) {
      return;
    }
    try {
      await evaluators.update(detail.id, { name: next });
      setDetail((current) => (current ? { ...current, name: next } : current));
    } catch (err) {
      setError(err);
    }
  };

  const commitDescription = async () => {
    setEditingDescription(false);
    const next = descriptionValue.trim();
    if (next === detail.description) {
      return;
    }
    try {
      await evaluators.update(detail.id, { description: next });
      setDetail((current) => (current ? { ...current, description: next } : current));
    } catch (err) {
      setError(err);
    }
  };

  const confirmDeleteVersion = async () => {
    if (!selected) {
      return;
    }
    setDeleteVersionBusy(true);
    setDeleteVersionError(null);
    try {
      await evaluators.deleteVersion(detail.id, selected.id);
      setDeleteVersionOpen(false);
      await load();
    } catch (err) {
      // A ReferencedError is rendered by ConfirmDialog from this error prop; keep the
      // dialog open so the user sees the blocking run count.
      setDeleteVersionError(err);
    } finally {
      setDeleteVersionBusy(false);
    }
  };

  const confirmDeleteEvaluator = async () => {
    setDeleteEvaluatorBusy(true);
    setDeleteEvaluatorError(null);
    try {
      await evaluators.remove(detail.id);
      navigate("/evaluators");
    } catch (err) {
      setDeleteEvaluatorError(err);
    } finally {
      setDeleteEvaluatorBusy(false);
    }
  };

  return (
    <section className="evaluator-detail">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {/* PageHeader owns the <h1>, so the click that opens the inline editors can no
          longer live on the title/description nodes themselves — a click on the heading
          targets the <h1>, not its child. Delegating from a wrapper lets the click reach
          a handler while keeping PageHeader's single-heading contract intact. */}
      <div
        onClick={(event) => {
          const el = event.target as HTMLElement;
          // Buttons and the editors' own inputs handle their own clicks; only the
          // static title/description text should switch into edit mode.
          if (el.closest("input, button, a")) {
            return;
          }
          if (el.closest("h1")) {
            setTitleValue(detail.name);
            setEditingTitle(true);
          } else if (el.closest("p")) {
            setDescriptionValue(detail.description);
            setEditingDescription(true);
          }
        }}
      >
        <PageHeader
          title={
            editingTitle ? (
              <input
                className="editable-title-input"
                aria-label="Evaluator name"
                autoFocus
                value={titleValue}
                onChange={(event) => setTitleValue(event.target.value)}
                onBlur={() => void commitTitle()}
              />
            ) : (
              <span className="editable-title">{detail.name}</span>
            )
          }
          description={
            editingDescription ? (
              <input
                className="editable-title-input"
                aria-label="Description"
                autoFocus
                value={descriptionValue}
                onChange={(event) => setDescriptionValue(event.target.value)}
                onBlur={() => void commitDescription()}
              />
            ) : (
              <span className="detail-description editable-title">
                {detail.description || "Add a description…"}
              </span>
            )
          }
          action={
            <div className="detail-header-actions">
              <Button variant="danger" onClick={() => setDeleteEvaluatorOpen(true)}>
                Delete evaluator
              </Button>
              <Button variant="secondary" onClick={() => navigate("/evaluators")}>
                Back
              </Button>
            </div>
          }
        />
      </div>

      {detail.versions.length > 0 && (
        <div className="version-bar">
          <Select
            aria-label="Version"
            value={selectedId ?? ""}
            options={detail.versions.map((version) => ({
              value: version.id,
              label: `${version.version_name}${version.frozen ? " (frozen)" : ""}`,
            }))}
            onChange={(event) => {
              setDraft(false);
              setSelectedId(event.target.value);
            }}
          />
          {!showDraft && selected?.frozen && <Badge tone="warning">Frozen</Badge>}
          {!showDraft && selected?.id === detail.active_version_id && (
            <Badge tone="success">Active</Badge>
          )}
          <div className="version-bar-actions">
            <Button variant="secondary" onClick={() => setDraft(true)}>
              New version
            </Button>
            <Button
              variant="secondary"
              onClick={() => setExporting(true)}
              disabled={showDraft || !selected}
            >
              Export
            </Button>
            <Button
              variant="secondary"
              onClick={() => setGenerating(true)}
              disabled={showDraft || !selected}
            >
              Generate dataset
            </Button>
            <Button
              variant="secondary"
              onClick={() => setDeleteVersionOpen(true)}
              disabled={showDraft || !selected}
            >
              Delete version
            </Button>
            <Button
              variant="secondary"
              onClick={() => navigate("/runs")}
              disabled={showDraft || !selected}
            >
              Run
            </Button>
          </div>
        </div>
      )}

      {showDraft ? (
        <VersionEditor
          key="draft"
          version={null}
          evaluatorId={detail.id}
          config={config}
          evaluatorName={detail.name}
          onSaved={(version) => {
            setDraft(false);
            void load(version.id);
          }}
        />
      ) : (
        selected && (
          <VersionEditor
            key={selected.id}
            version={selected}
            evaluatorId={detail.id}
            config={config}
            evaluatorName={detail.name}
            onSaved={(version) => void load(version.id)}
          />
        )
      )}

      {!showDraft && selected && exporting && (
        <ExportModal
          open={exporting}
          evaluatorId={detail.id}
          versionId={selected.id}
          onClose={() => setExporting(false)}
        />
      )}

      {!showDraft && selected && generating && (
        <DatasetFromEvaluator
          open={generating}
          version={selected}
          maxCount={200}
          onClose={() => setGenerating(false)}
        />
      )}

      <ConfirmDialog
        open={deleteVersionOpen}
        title="Delete version"
        message={`Delete version ${selected?.version_name ?? ""}? This cannot be undone.`}
        busy={deleteVersionBusy}
        error={deleteVersionError}
        onConfirm={() => void confirmDeleteVersion()}
        onClose={() => {
          setDeleteVersionOpen(false);
          setDeleteVersionError(null);
        }}
      />

      <ConfirmDialog
        open={deleteEvaluatorOpen}
        title="Delete evaluator"
        message={`Delete ${detail.name}? This deletes all of its versions.`}
        busy={deleteEvaluatorBusy}
        error={deleteEvaluatorError}
        onConfirm={() => void confirmDeleteEvaluator()}
        onClose={() => {
          setDeleteEvaluatorOpen(false);
          setDeleteEvaluatorError(null);
        }}
      />
    </section>
  );
}
