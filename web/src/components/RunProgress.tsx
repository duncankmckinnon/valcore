// Live view of an in-flight run. Subscribes to the run's SSE stream and reflects
// progress, per-row ticks, and elapsed time until a terminal event arrives.
//
// The API replays the current status and completed-row count on connect, so this
// component initializes those from the replayed `status` event rather than assuming
// zero — a page refresh mid-run therefore reattaches at the right progress.

import { useEffect, useRef, useState } from "react";
import { runs } from "../api/client";
import type { RunStatus, RunStreamEvent } from "../api/types";
import { Badge, Button, ErrorBanner, Spinner } from "./ui";

type Tick = { row_id: string; success: boolean; score_value: string | number | null };

type Props = {
  runId: string;
  total: number;
  startedAt?: string | null;
  onFinished?: (status: RunStatus) => void;
};

const TERMINAL: ReadonlySet<RunStatus> = new Set<RunStatus>([
  "completed",
  "completed_with_errors",
  "cancelled",
  "failed",
]);

const STATUS_TONE: Record<RunStatus, "neutral" | "success" | "warning" | "danger"> = {
  pending: "neutral",
  running: "neutral",
  completed: "success",
  completed_with_errors: "warning",
  cancelled: "neutral",
  failed: "danger",
};

function formatElapsed(seconds: number): string {
  const mm = Math.floor(seconds / 60);
  const ss = seconds % 60;
  return `${mm}:${String(ss).padStart(2, "0")}`;
}

export default function RunProgress({ runId, total, startedAt, onFinished }: Props) {
  const [status, setStatus] = useState<RunStatus>("pending");
  const [completed, setCompleted] = useState(0);
  const [knownTotal, setKnownTotal] = useState(total);
  const [ticks, setTicks] = useState<Tick[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const onFinishedRef = useRef(onFinished);
  onFinishedRef.current = onFinished;

  useEffect(() => {
    setKnownTotal(total);
  }, [total]);

  useEffect(() => {
    const unsubscribe = runs.streamEvents(runId, (event: RunStreamEvent) => {
      switch (event.type) {
        case "status":
          if (event.completed !== undefined) setCompleted(event.completed);
          if (event.status) setStatus(event.status);
          break;
        case "started":
          if (event.total !== undefined) setKnownTotal(event.total);
          setStatus("running");
          break;
        case "row":
          setCompleted((c) => c + 1);
          setTicks((prev) =>
            [
              {
                row_id: event.row_id ?? "",
                success: event.success ?? true,
                score_value: event.score_value ?? null,
              },
              ...prev,
            ].slice(0, 20),
          );
          break;
        case "finished": {
          const next = (event.status ?? "completed") as RunStatus;
          setStatus(next);
          onFinishedRef.current?.(next);
          break;
        }
        case "error":
          setStatus("failed");
          setError(new Error(event.error ?? "Run failed"));
          onFinishedRef.current?.("failed");
          break;
      }
    });
    return unsubscribe;
  }, [runId]);

  // Elapsed clock, anchored to the run's start when known. Stops once terminal.
  const isTerminal = TERMINAL.has(status);
  useEffect(() => {
    if (isTerminal) return;
    const base = startedAt ? new Date(startedAt).getTime() : Date.now();
    const tick = () => setElapsed(Math.max(0, Math.floor((Date.now() - base) / 1000)));
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [isTerminal, startedAt]);

  async function cancel() {
    setCancelling(true);
    setError(null);
    try {
      await runs.cancel(runId);
    } catch (err) {
      setError(err);
      setCancelling(false);
    }
  }

  const pct = knownTotal > 0 ? Math.min(100, Math.round((completed / knownTotal) * 100)) : 0;

  return (
    <div className="run-progress">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <div className="run-progress-header">
        <Badge tone={STATUS_TONE[status]}>{status.replace(/_/g, " ")}</Badge>
        <span className="progress-count">
          {completed} / {knownTotal}
        </span>
        <span className="muted">{formatElapsed(elapsed)}</span>
        {!isTerminal && (
          <Button variant="danger" onClick={cancel} disabled={cancelling}>
            {cancelling ? <Spinner /> : "Cancel"}
          </Button>
        )}
      </div>

      <div className="progress-bar" role="progressbar" aria-valuenow={pct}>
        <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
      </div>

      {ticks.length > 0 && (
        <ul className="tick-list">
          {ticks.map((tick, i) => (
            <li key={`${tick.row_id}-${i}`} className={tick.success ? "tick-ok" : "tick-error"}>
              <span className="tick-dot" aria-hidden />
              {tick.success ? String(tick.score_value ?? "") : "error"}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
