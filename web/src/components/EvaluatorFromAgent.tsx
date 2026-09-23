import { useState } from "react";
import { evaluators } from "../api/client";
import type { AgentVersion, GeneratedConfig, LabelSchema } from "../api/types";
import { ColumnNotesEditor } from "./ColumnNotesEditor";
import LabelSchemaEditor from "./LabelSchemaEditor";
import { GATEWAY_BLOCKER, useSetup } from "./useSetup";
import { Button, ErrorBanner, Modal, TextArea } from "./ui";

type Props = {
  open: boolean;
  version: AgentVersion;
  onGenerated: (draft: GeneratedConfig) => void;
  onClose: () => void;
};

const DEFAULT_SCHEMA: LabelSchema = {
  kind: "categorical",
  labels: [],
  minimum: null,
  maximum: null,
};

export default function EvaluatorFromAgent({ open, version, onGenerated, onClose }: Props) {
  const inputs = version.required_columns.length ? version.required_columns : ["input"];
  const outputs = version.response_columns;
  const columns = [...new Set([...inputs, ...outputs])];
  const [selected, setSelected] = useState(columns);
  const [criteria, setCriteria] = useState("");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [prescribeLabels, setPrescribeLabels] = useState(false);
  const [labelSchema, setLabelSchema] = useState<LabelSchema>(DEFAULT_SCHEMA);
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const { gatewayReady } = useSetup();

  const canSubmit =
    criteria.trim() !== "" &&
    selected.some((name) => inputs.includes(name)) &&
    selected.some((name) => outputs.includes(name)) &&
    gatewayReady &&
    !submitting;

  async function submit(): Promise<void> {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const draft = await evaluators.generate({
        criteria: criteria.trim(),
        agent_version_id: version.id,
        columns: selected,
        column_notes: Object.fromEntries(
          Object.entries(notes).filter(([name, note]) => selected.includes(name) && note.trim()),
        ),
        ...(prescribeLabels ? { label_schema: labelSchema } : {}),
      });
      onGenerated(draft);
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={open}
      title="Generate evaluator from agent"
      description="The evaluator draft uses the agent's input and response fields. Review it before saving."
      size="lg"
      onClose={onClose}
      footer={
        <div className="modal-actions">
          {!gatewayReady && <span className="form-footer-blocker">{GATEWAY_BLOCKER}</span>}
          <Button variant="secondary" onClick={onClose} disabled={submitting}>Cancel</Button>
          <Button variant="primary" onClick={() => void submit()} disabled={!canSubmit}>
            Generate evaluator
          </Button>
        </div>
      }
    >
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <label className="field">
        <span className="field-label">Criteria</span>
        <TextArea
          aria-label="Criteria"
          rows={6}
          placeholder="What makes this agent's response good?"
          value={criteria}
          onChange={(event) => setCriteria(event.target.value)}
        />
      </label>
      <div className="field">
        <span className="field-label">Agent contract fields</span>
        <ColumnNotesEditor
          lockedColumns={columns}
          extraColumns={[]}
          notes={notes}
          notePlaceholder="How should this field factor into the assessment?"
          lockedBadge="agent contract"
          allowAddColumns={false}
          selectedColumns={selected}
          onChangeSelected={setSelected}
          onChangeNotes={setNotes}
          onChangeExtraColumns={() => {}}
        />
        <p className="muted">Include at least one input and one response field.</p>
      </div>
      <div className="field">
        <label className="field-inline">
          <input
            type="checkbox"
            checked={prescribeLabels}
            onChange={(event) => setPrescribeLabels(event.target.checked)}
          />
          Prescribe a label space
        </label>
        {prescribeLabels && <LabelSchemaEditor value={labelSchema} onChange={setLabelSchema} />}
      </div>
    </Modal>
  );
}
