// Runs index: a table of every run plus the RunLauncher. When the route carries an
// :id the single-run detail view is shown; the /runs/compare route shows ComparePage.

import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { datasets, evaluators, runs } from "../api/client";
import type { Dataset, Evaluator, EvaluatorVersion, Run, RunStatus } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { RunIcon } from "../components/icons";
import { PageHeader } from "../components/PageHeader";
import RunLauncher from "../components/RunLauncher";
import { Badge, Button, ErrorBanner, Modal, Spinner, Table } from "../components/ui";
import ComparePage from "./ComparePage";
import RunDetail from "./RunDetail";

type EvaluatorWithVersions = Evaluator & { versions: EvaluatorVersion[] };

const STATUS_TONE: Record<RunStatus, "neutral" | "success" | "warning" | "danger"> = {
  pending: "neutral",
  running: "neutral",
  completed: "success",
  completed_with_errors: "warning",
  cancelled: "neutral",
  failed: "danger",
};

function headlineMetric(metrics: Record<string, unknown> | null): string {
  if (!metrics) return "—";
  if (typeof metrics.accuracy === "number") return `acc ${(metrics.accuracy * 100).toFixed(0)}%`;
  if (typeof metrics.mae === "number") return `MAE ${metrics.mae.toFixed(2)}`;
  return "—";
}

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "—";
}

export default function RunsPage() {
  const { id } = useParams();
  const location = useLocation();
  if (location.pathname === "/runs/compare") return <ComparePage />;
  if (id) return <RunDetail runId={id} />;
  return <RunsList />;
}

function RunsList() {
  const navigate = useNavigate();
  const [runList, setRunList] = useState<Run[] | null>(null);
  const [versionNames, setVersionNames] = useState<Record<string, string>>({});
  const [datasetNames, setDatasetNames] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(null);
  const [launching, setLaunching] = useState(false);

  useEffect(() => {
    runs.list().then(setRunList).catch(setError);
    datasets
      .list()
      .then((all) => setDatasetNames(Object.fromEntries(all.map((d: Dataset) => [d.id, d.name]))))
      .catch(setError);
    // Resolve each version id to "<evaluator> / <version>" by loading every evaluator's
    // versions once. Cheap at the single-user scale this tool targets.
    evaluators
      .list()
      .then(async (all) => {
        const details = await Promise.all(
          all.map((e) => evaluators.get(e.id) as unknown as Promise<EvaluatorWithVersions>),
        );
        const names: Record<string, string> = {};
        for (const detail of details) {
          for (const version of detail.versions) {
            names[version.id] = `${detail.name} / ${version.version_name}`;
          }
        }
        setVersionNames(names);
      })
      .catch(setError);
  }, []);

  const rows = useMemo(() => runList ?? [], [runList]);

  return (
    <section>
      <PageHeader
        title="Runs"
        description="A run applies an evaluator version to a dataset and records how well it scored."
        action={
          <div className="form-actions">
            <Button onClick={() => setLaunching(true)}>New run</Button>
            <Button variant="secondary" onClick={() => navigate("/runs/compare")}>
              Compare
            </Button>
          </div>
        }
      />

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {runList === null ? (
        <Spinner />
      ) : (
        <Table<Run>
          rows={rows}
          rowKey={(run) => run.id}
          empty={
            <EmptyState
              icon={<RunIcon />}
              message="A run scores an evaluator version against a dataset. Start one to see it here."
              action={<Button onClick={() => setLaunching(true)}>New run</Button>}
            />
          }
          columns={[
            {
              header: "Evaluator / version",
              cell: (run) => (
                <Link to={`/runs/${run.id}`}>{versionNames[run.version_id] ?? run.version_id.slice(0, 8)}</Link>
              ),
            },
            { header: "Dataset", cell: (run) => datasetNames[run.dataset_id] ?? run.dataset_id.slice(0, 8) },
            { header: "Kind", cell: (run) => run.kind },
            {
              header: "Status",
              cell: (run) => <Badge tone={STATUS_TONE[run.status]}>{run.status.replace(/_/g, " ")}</Badge>,
            },
            { header: "Metric", cell: (run) => headlineMetric(run.metrics) },
            { header: "Started", cell: (run) => formatTime(run.started_at) },
          ]}
        />
      )}

      <Modal open={launching} title="New run" onClose={() => setLaunching(false)}>
        <RunLauncher
          onStarted={(run) => {
            setLaunching(false);
            navigate(`/runs/${run.id}`);
          }}
        />
      </Modal>
    </section>
  );
}
