// The landing page: a read-only snapshot of the workspace fetched in one request.
// It is the first screen a prospective user sees, so it doubles as an explanation of
// what valcore does and a set of next actions into the author -> label -> run -> gate flow.

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { overview } from "../api/client";
import type { Overview, SetupKey } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";
import { useSetup } from "../components/useSetup";
import { Button, ErrorBanner, Spinner } from "../components/ui";

// Both accuracy fields are 0..1 floats or null. Null is a genuine "no measurement"
// state, not zero — render an em dash so it never reads as 0% or NaN%.
function formatAccuracy(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

// Renders one key's row: label, required/optional badge, purpose, and its command with a Copy
// button. The command is always shown while the card is expanded — even for a key that is
// already set — so a user who sets the gateway key but skips Logfire still sees exactly what to
// run for the optional keys without re-expanding anything.
function SetupKeyRow({ item }: { item: SetupKey }): JSX.Element {
  const copy = () => {
    void navigator.clipboard.writeText(item.command);
  };

  return (
    <li className="setup-key">
      <span>{item.label}</span>
      <span className={item.required ? "setup-key-required" : "setup-key-optional"}>
        {item.required ? "Required" : "Optional"}
      </span>
      <span>{item.set ? "Set" : "Not set"}</span>
      <span>{item.purpose}</span>
      <div className="setup-command">
        <code>{item.command}</code>
        <Button variant="secondary" onClick={copy}>
          Copy
        </Button>
      </div>
    </li>
  );
}

// The setup walkthrough. Expanded (one row per key) whenever anything is unset, collapsed to a
// quiet summary once everything is configured — incomplete setup is itself the trigger, so it
// reappears on its own if a user clears their config. A still-loading or errored fetch renders
// nothing here; the rest of the page never waits on it.
function SetupCard(): JSX.Element | null {
  const { status, refetch } = useSetup();

  if (status === null) {
    return null;
  }

  const allSet = status.keys.every((key) => key.set);

  return (
    <div className="setup-card">
      {allSet ? (
        <div className="setup-summary">All setup keys are configured.</div>
      ) : (
        <ul>
          {status.keys.map((key) => (
            <SetupKeyRow key={key.name} item={key} />
          ))}
        </ul>
      )}
      <Button variant="secondary" onClick={refetch}>
        Recheck
      </Button>
    </div>
  );
}

export default function OverviewPage(): JSX.Element {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    overview.get().then(setData).catch(setError);
  }, []);

  if (error) {
    return (
      <section>
        <PageHeader title="Overview" />
        <ErrorBanner error={error} />
      </section>
    );
  }

  if (data === null) {
    return (
      <section>
        <PageHeader title="Overview" />
        <Spinner />
      </section>
    );
  }

  const description =
    "Author an LLM-as-judge evaluator, score it against a labeled dataset, and gate CI on its accuracy.";

  // A fresh install has nothing to show. Lead with the flow and a single action
  // rather than a wall of zeroes.
  const empty =
    data.evaluator_count === 0 && data.dataset_count === 0 && data.run_count === 0;

  if (empty) {
    return (
      <section>
        <PageHeader title="Overview" description={description} />
        <SetupCard />
        <div className="overview-empty">
          <EmptyState
            message="Start by authoring an evaluator, label a dataset to score it against, run it, and gate CI on the accuracy it reports."
            action={
              <Link className="btn btn-primary" to="/evaluators">
                Create your first evaluator
              </Link>
            }
          />
        </div>
      </section>
    );
  }

  return (
    <section>
      <PageHeader title="Overview" description={description} />

      <SetupCard />

      <div className="overview-stats">
        <div className="stat-card">
          <div className="stat-card-value">{data.evaluator_count}</div>
          <div className="stat-card-label">Evaluators</div>
        </div>
        <div className="stat-card">
          <div className="stat-card-value">{data.dataset_count}</div>
          <div className="stat-card-label">Datasets</div>
          <div className="stat-card-sub">{`${data.labeled_rows} of ${data.total_rows} labeled`}</div>
        </div>
        <div className="stat-card">
          <div className="stat-card-value">{formatAccuracy(data.best_accuracy)}</div>
          <div className="stat-card-label">Best accuracy</div>
        </div>
      </div>

      <div className="overview-next">
        <Link className="next-card" to="/evaluators">
          <div className="stat-card-label">Author an evaluator</div>
          <div className="stat-card-sub">
            Describe the judgement you want and generate a first version.
          </div>
        </Link>
        {data.latest_run ? (
          <Link className="next-card" to={`/runs/${data.latest_run.id}`}>
            <div className="stat-card-label">Latest run</div>
            <div className="stat-card-value">{data.latest_run.dataset_name}</div>
            <div className="stat-card-sub">
              {formatAccuracy(data.latest_run.accuracy)} · {data.latest_run.status.replace(/_/g, " ")}
            </div>
          </Link>
        ) : (
          <Link className="next-card" to="/datasets">
            <div className="stat-card-label">Create a dataset</div>
            <div className="stat-card-sub">
              Label some rows to score an evaluator against.
            </div>
          </Link>
        )}
      </div>
    </section>
  );
}
