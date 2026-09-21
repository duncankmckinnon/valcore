// The agents surface makes the systems being measured discoverable before their
// version-specific editing and trial work moves into AgentDetail.

import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { agents } from "../api/client";
import type { AgentSummary } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";
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
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<AgentSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

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

  const create = async (): Promise<void> => {
    if (name.trim() === "") return;
    setBusy(true);
    setError(null);
    try {
      const agent = await agents.create({ name, description });
      navigate(`/agents/${agent.id}`);
    } catch (err) {
      setError(err);
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
        onClose={() => setCreating(false)}
        footer={
          <div className="form-actions">
            <Button
              variant="secondary"
              onClick={() => setCreating(false)}
              disabled={busy}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => void create()}
              disabled={busy || name.trim() === ""}
            >
              {busy ? <Spinner /> : "Create"}
            </Button>
          </div>
        }
      >
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
  const { pathname } = useLocation();
  // The pathname fallback keeps this page independently renderable in embedding contexts
  // where a router exists but has not declared the matching parameterized route.
  const routeId = id ?? pathname.match(/^\/agents\/([^/]+)$/)?.[1];
  return routeId ? <AgentDetail agentId={routeId} /> : <AgentsList />;
}
