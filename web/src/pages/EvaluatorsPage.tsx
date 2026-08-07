// The evaluators list plus the "new evaluator" flow. When the route carries an :id this
// page defers to EvaluatorDetail. The new-evaluator form offers two modes: create an empty
// evaluator from scratch, or generate a first version from criteria. Both route to the
// detail page for editing.

import { useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { api, evaluators } from "../api/client";
import type { Evaluator, EvaluatorVersion, GeneratedConfig } from "../api/types";
import EvaluatorDetail from "./EvaluatorDetail";
import { VersionEditor } from "../components/VersionEditor";
import type { AppConfig } from "../components/VersionEditor";
import { Badge, Button, ErrorBanner, Modal, Spinner, Table, TextArea } from "../components/ui";
import { PageHeader } from "../components/PageHeader";
import { EmptyState } from "../components/EmptyState";
import { FormFooter } from "../components/FormFooter";
import { Tooltip } from "../components/Tooltip";
import { EvaluatorIcon } from "../components/icons";

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
  const location = useLocation();
  const navigate = useNavigate();
  const receivedDraft =
    (location.state as { draft?: GeneratedConfig } | null)?.draft ?? null;
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

  // One reason at a time, so a blocked primary action says why instead of sitting
  // silently disabled. Order mirrors the fields top to bottom.
  const blockers: string[] = [];
  if (name.trim() === "") {
    blockers.push("Add a name");
  }
  if (mode === "criteria" && criteria.trim() === "") {
    blockers.push("Describe the criteria");
  }

  const submit = async () => {
    if (blockers.length > 0) {
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

  if (receivedDraft && config) {
    return (
      <section>
        <PageHeader title={receivedDraft.name} />
        <VersionEditor
          version={null}
          evaluatorId=""
          config={config}
          evaluatorName={receivedDraft.name}
          initialDraft={receivedDraft}
          onCreateDraft={async (version) => {
            const evaluator = await evaluators.create({ name: receivedDraft.name });
            return evaluators.createVersion(evaluator.id, version);
          }}
          onSaved={(version) => navigate(`/evaluators/${version.evaluator_id}`)}
        />
      </section>
    );
  }

  return (
    <section>
      <PageHeader
        title="Evaluators"
        description="Reusable LLM-as-judge configs that score dataset rows."
        action={
          <Button variant="primary" onClick={() => setCreating(true)}>
            New evaluator
          </Button>
        }
      />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <Table<EvaluatorRow>
        rows={rows}
        rowKey={(row) => row.id}
        empty={
          <EmptyState
            icon={<EvaluatorIcon />}
            message="An evaluator is a prompt plus an output contract that grades rows."
            action={
              <Button variant="primary" onClick={() => setCreating(true)}>
                Create evaluator
              </Button>
            }
          />
        }
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

      <Modal
        open={creating}
        title="New evaluator"
        description="Start blank and write the first version yourself, or describe your criteria and let a model draft one."
        size="lg"
        onClose={() => setCreating(false)}
        footer={
          <FormFooter blockers={blockers}>
            <Button variant="secondary" onClick={() => setCreating(false)} disabled={busy}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={submit}
              disabled={busy || blockers.length > 0}
            >
              {busy ? <Spinner /> : mode === "scratch" ? "Create" : "Generate"}
            </Button>
          </FormFooter>
        }
      >
        <div className="modal-two-pane">
          <div>
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
              // Not a wrapping <label>: the Tooltip trigger is a second labelable
              // control, so the textarea leans on its aria-label alone to stay the one
              // element matched by "Criteria".
              <div className="field">
                <span className="field-label">
                  Criteria
                  <Tooltip text="Describe what a good response looks like. This becomes the first version of the evaluator." />
                </span>
                <TextArea
                  aria-label="Criteria"
                  rows={8}
                  placeholder="Describe what a good response looks like…"
                  value={criteria}
                  onChange={(event) => setCriteria(event.target.value)}
                />
              </div>
            )}
          </div>
          <aside className="modal-side">
            {mode === "criteria" ? (
              <>
                <p>What happens next:</p>
                <ol>
                  <li>A model drafts instructions, a prompt template, and output fields.</li>
                  <li>You land in the version editor to review the draft.</li>
                  <li>Nothing is saved until you choose to save.</li>
                </ol>
                <p>Generation takes several seconds.</p>
              </>
            ) : (
              <p>
                You get an empty evaluator and author the first version yourself.
              </p>
            )}
          </aside>
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
