import InvocationBindings from "./InvocationBindings";
import type { InvocationMapping } from "../api/types";
import { useEffect, useState } from "react";
import { agents, datasets, evaluators, runs } from "../api/client";
import type { AgentSummary, AgentVersion, DatasetSummary, Evaluator, EvaluatorVersion, Run } from "../api/types";
import { Button, ErrorBanner, Select, Spinner } from "./ui";
import { GATEWAY_BLOCKER, useSetup } from "./useSetup";

type EvaluatorDetail = Evaluator & { versions: EvaluatorVersion[] };

export default function AgentRunLauncher({ withEvaluators, onStarted }: {
  withEvaluators: boolean;
  onStarted: (run: Run) => void;
}) {
  const [datasetList, setDatasetList] = useState<DatasetSummary[]>([]);
  const [agentList, setAgentList] = useState<AgentSummary[]>([]);
  const [versions, setVersions] = useState<AgentVersion[]>([]);
  const [evaluatorList, setEvaluatorList] = useState<EvaluatorDetail[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [agentId, setAgentId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [concurrency, setConcurrency] = useState(8);
  const [submitting, setSubmitting] = useState(false);
  const [mapping, setMapping] = useState<InvocationMapping>({});
  const [error, setError] = useState<unknown>(null);
  const { gatewayReady } = useSetup();

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      datasets.list(), agents.list(),
      withEvaluators ? evaluators.list().then((items) => Promise.all(
        items.map((item) => evaluators.get(item.id) as Promise<EvaluatorDetail>),
      )) : Promise.resolve([]),
    ]).then(([data, subjects, judges]) => {
      if (cancelled) return;
      setDatasetList(data);
      setAgentList(subjects);
      setEvaluatorList(judges);
    }).catch((err) => !cancelled && setError(err));
    return () => { cancelled = true; };
  }, [withEvaluators]);

  useEffect(() => {
    let cancelled = false;
    setVersions([]);
    setVersionId("");
    if (agentId) {
      agents.listVersions(agentId).then((items) => {
        if (cancelled) return;
        setVersions(items);
        const active = agentList.find((agent) => agent.id === agentId)?.active_version_id;
        setVersionId(items.find((version) => version.id === active)?.id ?? items[0]?.id ?? "");
      }).catch((err) => !cancelled && setError(err));
    }
    return () => { cancelled = true; };
  }, [agentId, agentList]);

  const dataset = datasetList.find((item) => item.id === datasetId);
  const version = versions.find((item) => item.id === versionId);
  const columns = [...(dataset?.columns ?? []), ...(version?.response_columns ?? [])];
  const missingAgentColumns = version?.required_columns.filter((column) => !dataset?.columns.includes(column)) ?? [];
  const judges = evaluatorList.flatMap((item) =>
    item.versions.filter((candidate) => candidate.id === selected[item.id]));
  const missingColumns = (judge: EvaluatorVersion) => judge.required_columns.filter((column) => judge.input_contract === "invocation" ? !dataset?.columns.includes(mapping.metadata?.[column] ?? column) : !columns.includes(column));
  const metadataNames = [...new Set(judges.filter(judge => judge.input_contract === "invocation").flatMap(judge => judge.required_columns))];
  useEffect(() => setMapping({}), [datasetId]);
  const needsGateway = [version, ...judges].some((item) => item && !item.model.startsWith("local/"));
  const blockedByGateway = needsGateway && !gatewayReady;
  const canStart = dataset && version && missingAgentColumns.length === 0 &&
    Object.values(mapping.metadata ?? {}).every(column => dataset.columns.includes(column)) &&
    (!withEvaluators || (judges.length > 0 && judges.every((judge) => missingColumns(judge).length === 0))) &&
    Number.isInteger(concurrency) && concurrency >= 1 && concurrency <= 64 &&
    !submitting && !blockedByGateway;

  async function start() {
    if (!canStart) return;
    setSubmitting(true);
    setError(null);
    try {
      const run = await runs.create({
        ...(judges.some(judge => judge.input_contract === "invocation") && Object.keys(mapping).length ? { invocation_mapping: mapping } : {}),
        kind: "derive", version_id: versionId, dataset_id: datasetId, concurrency,
        ...(withEvaluators ? { evaluator_version_ids: judges.map((judge) => judge.id) } : {}),
      });
      onStarted(run);
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="run-launcher">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <label className="field">
        <span className="field-label">Dataset</span>
        <Select aria-label="Dataset" value={datasetId} onChange={(e) => setDatasetId(e.target.value)}
          options={[{ value: "", label: "Select a dataset…" }, ...datasetList.map((item) => ({ value: item.id, label: item.name }))]} />
      </label>
      <label className="field">
        <span className="field-label">Agent</span>
        <Select aria-label="Agent" value={agentId} onChange={(e) => setAgentId(e.target.value)}
          options={[{ value: "", label: "Select an agent…" }, ...agentList.map((item) => ({ value: item.id, label: item.name }))]} />
      </label>
      <label className="field">
        <span className="field-label">Agent version</span>
        <Select aria-label="Agent version" value={versionId} disabled={!versions.length}
          onChange={(e) => setVersionId(e.target.value)}
          options={versions.length ? versions.map((item) => ({ value: item.id, label: item.version_name })) : [{ value: "", label: "No version selected" }]} />
      </label>
      {dataset && missingAgentColumns.length > 0 && <p className="muted">Agent needs dataset columns: {missingAgentColumns.join(", ")}</p>}
      {withEvaluators && (
        <fieldset disabled={submitting}>
          <legend>Evaluators</legend>
          <p className="muted">Choose one or more evaluators. Responses are saved automatically, then evaluators run concurrently on each successful request/response pair. You can leave this page while they run.</p>
          {version && <p className="muted">Response columns: {version.response_columns.join(", ")}</p>}
          {evaluatorList.map((item) => {
            const checked = item.id in selected;
            const judge = item.versions.find((candidate) => candidate.id === selected[item.id]);
            return (
              <div className="field" key={item.id}>
                <label className="field-inline">
                  <input type="checkbox" checked={checked} disabled={!item.versions.length}
                    onChange={(event) => setSelected((prev) => {
                      const next = { ...prev };
                      if (event.target.checked) next[item.id] = item.versions.find((v) => v.id === item.active_version_id)?.id ?? item.versions[0].id;
                      else delete next[item.id];
                      return next;
                    })} />
                  {item.name}
                </label>
                {checked && <Select aria-label={item.name + " version"} value={selected[item.id]}
                  onChange={(e) => setSelected((prev) => ({ ...prev, [item.id]: e.target.value }))}
                  options={item.versions.map((candidate) => ({ value: candidate.id, label: candidate.version_name }))} />}
                {judge && missingColumns(judge).length > 0 && <span className="muted">Missing columns: {missingColumns(judge).join(", ")}</span>}
                {!item.versions.length && <span className="muted">Create an evaluator version first.</span>}
              </div>
            );
          })}
          {!evaluatorList.length && <p className="muted">No evaluators available.</p>}
        </fieldset>
      )}
      {withEvaluators && judges.some(judge => judge.input_contract === "invocation") && <InvocationBindings columns={dataset?.columns ?? []} required={metadataNames} mapping={mapping} onChange={setMapping} recorded metadataOnly />}
      <label className="field">
        <span className="field-label">Concurrency per run</span>
        <input className="select" type="number" min={1} max={64} aria-label="Concurrency"
          value={concurrency} onChange={(event) => setConcurrency(Number(event.target.value))} />
      </label>
      <div className="form-actions">
        {blockedByGateway && <span className="form-footer-blocker">{GATEWAY_BLOCKER}</span>}
        <Button disabled={!canStart} onClick={start}>{submitting ? <Spinner /> : "Start"}</Button>
      </div>
    </div>
  );
}
