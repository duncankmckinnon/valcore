// The agents surface makes the systems being measured discoverable before their
// version-specific editing and trial work moves into AgentDetail.

import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { agents } from "../api/client";
import type { AgentDraft, AgentSummary } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { FormFooter } from "../components/FormFooter";
import { PageHeader } from "../components/PageHeader";
import { Tooltip } from "../components/Tooltip";
import { GATEWAY_BLOCKER, useSetup } from "../components/useSetup";
import {
  Button,
  ConfirmDialog,
  ErrorBanner,
  Modal,
  Spinner,
  Table,
  TextArea,
} from "../components/ui";
import AgentDetail from "./AgentDetail";

/** Renders the top-level list and creation flow for measured agents. */
function AgentsList(): JSX.Element {
  const navigate = useNavigate();
  const [rows, setRows] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [submitError, setSubmitError] = useState<unknown>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<AgentSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"scratch" | "prompt">("scratch");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [prompt, setPrompt] = useState("");
  const { gatewayReady } = useSetup();

  const load = async (): Promise<void> => {
    setLoading(true);
    try {
      setRows(await agents.list());
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const blockers: string[] = [];
  if (mode === "prompt" && !gatewayReady) blockers.push(GATEWAY_BLOCKER);
  if (name.trim() === "") blockers.push("Add a name");
  if (mode === "prompt" && prompt.trim() === "") blockers.push("Describe the agent");

  const closeCreate = (): void => {
    setCreating(false);
    setSubmitError(null);
  };

  const submit = async (): Promise<void> => {
    if (blockers.length > 0) return;
    setBusy(true);
    setSubmitError(null);
    try {
      if (mode === "scratch") {
        const agent = await agents.create({ name, description });
        navigate(`/agents/${agent.id}`);
      } else {
        // Draft first, so a failed generation never leaves an empty agent behind.
        const draft = await agents.generate({ prompt });
        const agent = await agents.create({ name, description });
        navigate(`/agents/${agent.id}`, { state: { draft } });
      }
    } catch (err) {
      // Keep the failure inside the dialog. The page banner sits under the backdrop.
      setSubmitError(err);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (): Promise<void> => {
    if (!deleting) return;
    setBusy(true);
    setError(null);
    try {
      await agents.remove(deleting.id);
      setDeleting(null);
      await load();
    } catch (err) {
      // Keep the API's dependency explanation visible instead of replacing it with
      // ConfirmDialog's evaluator-specific run-count wording.
      setError(err);
      setDeleting(null);
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <Spinner />;

  return (
    <section>
      <PageHeader
        title="Agents"
        description="Versioned definitions for the agents whose responses you want to measure."
        action={
          <Button variant="primary" onClick={() => setCreating(true)}>
            New agent
          </Button>
        }
      />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <Table<AgentSummary>
        rows={rows}
        rowKey={(row) => row.id}
        empty={
          <EmptyState
            message="An agent is the agent being measured, unlike an evaluator that scores it."
            action={
              <Button variant="primary" onClick={() => setCreating(true)}>
                Create agent
              </Button>
            }
          />
        }
        columns={[
          {
            header: "Name",
            cell: (row) => <Link to={`/agents/${row.id}`}>{row.name}</Link>,
          },
          {
            header: "Description",
            cell: (row) => row.description,
          },
          { header: "Versions", cell: (row) => row.version_count },
          {
            header: "",
            cell: (row) => (
              <Button variant="secondary" onClick={() => setDeleting(row)}>
                Delete {row.name}
              </Button>
            ),
          },
        ]}
      />
      <Modal
        open={creating}
        title="New agent"
        description="Start blank and write the first version yourself, or describe the agent and let a model draft one."
        size="lg"
        onClose={closeCreate}
        footer={
          <FormFooter blockers={blockers}>
            <Button
              variant="secondary"
              onClick={closeCreate}
              disabled={busy}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => void submit()}
              disabled={busy || blockers.length > 0}
            >
              {busy ? <Spinner /> : mode === "scratch" ? "Create" : "Generate"}
            </Button>
          </FormFooter>
        }
      >
        <ErrorBanner error={submitError} onDismiss={() => setSubmitError(null)} />
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
                aria-selected={mode === "prompt"}
                className={`mode-tab ${mode === "prompt" ? "mode-tab-active" : ""}`.trim()}
                onClick={() => setMode("prompt")}
              >
                From prompt
              </button>
            </div>
            <label className="field">
              <span className="field-label">Name</span>
              <input
                className="input"
                aria-label="Agent name"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <label className="field">
              <span className="field-label">Description</span>
              <TextArea
                aria-label="Description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </label>
            {mode === "prompt" && (
              <div className="field">
                <span className="field-label">
                  Prompt
                  <Tooltip text="Used only to draft the first version; it is not saved." />
                </span>
                <TextArea
                  aria-label="Prompt"
                  rows={8}
                  placeholder="Describe what the agent should do, what it receives, and what it should return…"
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value)}
                />
              </div>
            )}
          </div>
          <aside className="modal-side">
            {mode === "prompt" ? (
              <>
                <p>What happens next:</p>
                <ol>
                  <li>A model drafts instructions, a prompt template, capabilities, and any output fields.</li>
                  <li>You land on the agent's page with the draft in the editor.</li>
                  <li>The version isn't saved until you click Create version.</li>
                </ol>
                <p>Generation takes several seconds.</p>
              </>
            ) : (
              <p>You get an empty agent and author the first version yourself.</p>
            )}
          </aside>
        </div>
      </Modal>
      <ConfirmDialog
        open={deleting !== null}
        title="Delete agent"
        message={`Delete ${deleting?.name ?? ""}? This deletes all of its versions.`}
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </section>
  );
}

/** Selects the agent detail view when the shared route includes an agent id. */
export default function AgentsPage(): JSX.Element {
  const { id } = useParams();
  const location = useLocation();
  const draft = (location.state as { draft?: AgentDraft } | null)?.draft;
  return id ? <AgentDetail agentId={id} initialDraft={draft} /> : <AgentsList />;
}
