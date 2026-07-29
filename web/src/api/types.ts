// TypeScript mirrors of the API DTOs. These are kept in lockstep with the SQLModel
// entities and Pydantic schemas in `src/evalcore/` and `api/`.

export type ScoreKind = "categorical" | "numeric";
export type LabelSource = "manual" | "accepted" | "generated";
export type RunKind = "validation" | "eval";
export type RunStatus =
  | "pending"
  | "running"
  | "completed"
  | "completed_with_errors"
  | "cancelled"
  | "failed";
export type FieldType = "str" | "int" | "float" | "bool" | "enum";

export interface OutputField {
  name: string;
  type: FieldType;
  description: string;
  required: boolean;
  enum_values: string[] | null;
  minimum: number | null;
  maximum: number | null;
}

export interface CapabilitySpec {
  name: string;
  config: Record<string, unknown>;
}

export interface LabelSchema {
  kind: ScoreKind;
  labels: string[] | null;
  minimum: number | null;
  maximum: number | null;
}

export interface Evaluator {
  id: string;
  created_at: string;
  name: string;
  description: string;
  active_version_id: string | null;
}

export interface EvaluatorVersion {
  id: string;
  created_at: string;
  evaluator_id: string;
  version_name: string;
  notes: string;
  frozen: boolean;
  model: string;
  instructions: string;
  prompt_template: string;
  required_columns: string[];
  output_fields: OutputField[];
  score_field: string;
  score_kind: ScoreKind;
  score_labels: string[] | null;
  score_minimum: number | null;
  score_maximum: number | null;
  capabilities: CapabilitySpec[];
  tools: string[];
}

export interface Dataset {
  id: string;
  created_at: string;
  name: string;
  description: string;
  columns: string[];
  label_schema: LabelSchema;
}

export interface DatasetRow {
  id: string;
  created_at: string;
  dataset_id: string;
  idx: number;
  data: Record<string, unknown>;
  label: Record<string, unknown> | null;
  suggested_label: Record<string, unknown> | null;
  label_reasoning: string | null;
  label_source: LabelSource | null;
  note: string | null;
}

export interface Run {
  id: string;
  created_at: string;
  kind: RunKind;
  version_id: string;
  dataset_id: string;
  status: RunStatus;
  concurrency: number;
  started_at: string | null;
  finished_at: string | null;
  metrics: Record<string, unknown> | null;
  error: string | null;
  cancel_requested: boolean;
}

export interface RunResult {
  id: string;
  created_at: string;
  run_id: string;
  row_id: string;
  output: Record<string, unknown> | null;
  score_value: string | number | null;
  agreement: boolean | number | null;
  latency_ms: number | null;
  usage: Record<string, unknown> | null;
  error: string | null;
}

export interface GeneratedConfig {
  name: string;
  version_name: string;
  instructions: string;
  prompt_template: string;
  required_columns: string[];
  output_fields: OutputField[];
  score_field: string;
  score_kind: ScoreKind;
  score_labels: string[] | null;
  score_minimum: number | null;
  score_maximum: number | null;
  capabilities: CapabilitySpec[];
  tools: string[];
  rationale: string;
}

export interface RefinedConfig {
  config: GeneratedConfig;
  changed_fields: string[];
  summary: string;
}

export interface ErrorBody {
  error: {
    type: string;
    message: string;
  };
}
