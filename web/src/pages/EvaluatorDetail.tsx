// Detail view for a single evaluator: a version selector across the top, the VersionEditor
// below, and the Export / Run actions.

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, evaluators } from "../api/client";
import type { EvaluatorVersion } from "../api/types";
import { ExportModal } from "../components/ExportModal";
import type { AppConfig } from "../components/VersionEditor";
import { VersionEditor } from "../components/VersionEditor";
import { Badge, Button, ErrorBanner, Select, Spinner } from "../components/ui";

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
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);

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
          return data.versions[0]?.id ?? null;
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

  return (
    <section className="evaluator-detail">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <div className="detail-header">
        <div>
          <h1>{detail.name}</h1>
          <p className="detail-description">{detail.description}</p>
        </div>
        <Button variant="secondary" onClick={() => navigate("/evaluators")}>
          Back
        </Button>
      </div>

      {detail.versions.length === 0 ? (
        <p>No versions yet.</p>
      ) : (
        <>
          <div className="version-bar">
            <Select
              aria-label="Version"
              value={selectedId ?? ""}
              options={detail.versions.map((version) => ({
                value: version.id,
                label: `${version.version_name}${version.frozen ? " (frozen)" : ""}`,
              }))}
              onChange={(event) => setSelectedId(event.target.value)}
            />
            {selected?.frozen && <Badge tone="warning">Frozen</Badge>}
            {selected?.id === detail.active_version_id && <Badge tone="success">Active</Badge>}
            <div className="version-bar-actions">
              <Button variant="secondary" onClick={() => setExporting(true)} disabled={!selected}>
                Export
              </Button>
              <Button
                variant="secondary"
                onClick={() => navigate("/runs")}
                disabled={!selected}
              >
                Run
              </Button>
            </div>
          </div>

          {selected && (
            <VersionEditor
              key={selected.id}
              version={selected}
              config={config}
              evaluatorName={detail.name}
              onSaved={(version) => void load(version.id)}
            />
          )}

          {selected && exporting && (
            <ExportModal
              open={exporting}
              evaluatorId={detail.id}
              versionId={selected.id}
              onClose={() => setExporting(false)}
            />
          )}
        </>
      )}
    </section>
  );
}
