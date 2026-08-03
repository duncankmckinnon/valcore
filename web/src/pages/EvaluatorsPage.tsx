// The evaluators list plus the "new evaluator" flow. When the route carries an :id this
// page defers to EvaluatorDetail. The new-evaluator form offers two modes: create an empty
// evaluator from scratch, or generate a first version from criteria. Both route to the
// detail page for editing.

import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, evaluators } from "../api/client";
import type { Evaluator, EvaluatorVersion, GeneratedConfig } from "../api/types";
import EvaluatorDetail from "./EvaluatorDetail";
import type { AppConfig } from "../components/VersionEditor";
import { Badge, Button, ErrorBanner, Modal, Spinner, Table, TextArea } from "../components/ui";

type EvaluatorRow = Evaluator & {
  active_version: { version_name: string } | null;
  version_count?: number;
};

type NewMode = "scratch" | "criteria";

function draftToVersion(draft: GeneratedConfig, model: string): Partial<EvaluatorVersion> {
  return {
    version_name: draft.version_name,
    model,
    instructions: draft.instructions,
    prompt_template: draft.prompt_template,
    required_columns: draft.required_columns,
    output_fields: draft.output_fields,
    score_field: draft.score_field,
    score_kind: draft.score_kind,
    score_labels: draft.score_labels,
    score_minimum: draft.score_minimum,
    score_maximum: draft.score_maximum,
    capabilities: draft.capabilities,
    tools: draft.tools,
  };
}

function EvaluatorsList() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<EvaluatorRow[]>([]);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<NewMode>("scratch");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [criteria, setCriteria] = useState("");

  useEffect(() => {
    evaluators
      .list()
      .then((data) => setRows(data as unknown as EvaluatorRow[]))
      .catch(setError)
      .finally(() => setLoading(false));
    api<AppConfig>("/api/config").then(setConfig).catch(setError);
  }, []);

  const valid =
    mode === "scratch"
      ? name.trim() !== ""
      : name.trim() !== "" && criteria.trim() !== "";

  const submit = async () => {
    if (!valid) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (mode === "scratch") {
        const evaluator = await evaluators.create({ name, description });
        navigate(`/evaluators/${evaluator.id}`);
      } else {
        if (!config) {
          return;
        }
        const draft = await evaluators.generate({ criteria });
        const evaluator = await evaluators.create({ name });
        const model = config.models[0] ?? "";
        await evaluators.createVersion(evaluator.id, draftToVersion(draft, model));
        navigate(`/evaluators/${evaluator.id}`);
      }
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return <Spinner />;
  }

  return (
    <section>
      <div className="page-header">
        <h1>Evaluators</h1>
        <Button variant="primary" onClick={() => setCreating(true)}>
          New evaluator
        </Button>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <Table<EvaluatorRow>
        rows={rows}
        rowKey={(row) => row.id}
        empty="No evaluators yet."
        columns={[
          {
            header: "Name",
            cell: (row) => (
              <button className="link-button" onClick={() => navigate(`/evaluators/${row.id}`)}>
                {row.name}
              </button>
            ),
          },
          { header: "Description", cell: (row) => row.description },
          {
            header: "Active version",
            cell: (row) =>
              row.active_version ? (
                <Badge>{row.active_version.version_name}</Badge>
              ) : (
                <span className="muted">none</span>
              ),
          },
          { header: "Versions", cell: (row) => row.version_count ?? "—" },
        ]}
      />

      <Modal open={creating} title="New evaluator" onClose={() => setCreating(false)}>
        <div className="mode-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "scratch"}
            className={`mode-tab ${mode === "scratch" ? "mode-tab-active" : ""}`.trim()}
            onClick={() => setMode("scratch")}
          >
            From scratch
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "criteria"}
            className={`mode-tab ${mode === "criteria" ? "mode-tab-active" : ""}`.trim()}
            onClick={() => setMode("criteria")}
          >
            From criteria
          </button>
        </div>
        <label className="field">
          <span className="field-label">Name</span>
          <input
            className="input"
            aria-label="Evaluator name"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        {mode === "scratch" ? (
          <label className="field">
            <span className="field-label">Description</span>
            <TextArea
              aria-label="Description"
              rows={4}
              placeholder="What does this evaluator check?"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </label>
        ) : (
          <label className="field">
            <span className="field-label">Criteria</span>
            <TextArea
              aria-label="Criteria"
              rows={8}
              placeholder="Describe what a good response looks like…"
              value={criteria}
              onChange={(event) => setCriteria(event.target.value)}
            />
          </label>
        )}
        <div className="modal-actions">
          <Button variant="secondary" onClick={() => setCreating(false)} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" onClick={submit} disabled={busy || !valid}>
            {busy ? <Spinner /> : mode === "scratch" ? "Create" : "Generate"}
          </Button>
        </div>
      </Modal>
    </section>
  );
}

export default function EvaluatorsPage() {
  const { id } = useParams();
  if (id) {
    return <EvaluatorDetail id={id} />;
  }
  return <EvaluatorsList />;
}
