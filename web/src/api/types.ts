// TypeScript mirrors of the API DTOs. These are kept in lockstep with the SQLModel
// entities and Pydantic schemas in `src/valcore/` and `api/`.

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

export type EmptyLabelSchema = Record<string, never>;

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
  label_schema: LabelSchema | EmptyLabelSchema;
  row_count: number;
  labeled_count: number;
}

// The most recent run, surfaced on the overview landing page. `accuracy` is read
// server-side out of the run's untyped metrics and returned as a typed float.
export interface LatestRun {
  id: string;
  dataset_name: string;
  status: RunStatus;
  accuracy: number | null;
  finished_at: string | null;
}

// A read-only snapshot of the workspace for the landing page. Every count is
// server-computed; `best_accuracy` and `latest_run` are null on an empty workspace.
export interface Overview {
  evaluator_count: number;
  dataset_count: number;
  run_count: number;
  total_rows: number;
  labeled_rows: number;
  best_accuracy: number | null;
  latest_run: LatestRun | null;
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

export interface RowsPage {
  rows: DatasetRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface RowPatch {
  label?: string | number | null;
  note?: string | null;
  accept_suggestion?: boolean;
  clear_label?: boolean;
  data?: Record<string, unknown>;
}

export interface DatasetStats {
  total: number;
  labeled: number;
  unlabeled: number;
  label_distribution: Record<string, number>;
}

export interface DatasetCreated {
  dataset: Dataset;
  row_count: number;
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

// A run result joined with its row's data and human label, as returned by
// `GET /api/runs/{id}/results`.
export interface ResultRow {
  result_id: string;
  row_id: string;
  idx: number;
  data: Record<string, unknown>;
  output: Record<string, unknown> | null;
  score_value: string | number | null;
  agreement: boolean | number | null;
  error: string | null;
  latency_ms: number | null;
  label: string | number | null;
}

export interface ResultsPage {
  results: ResultRow[];
  total: number;
  limit: number;
  offset: number;
}

// One dataset row scored by both compared runs, with the human label.
export interface CompareRow {
  row_id: string;
  idx: number;
  data: Record<string, unknown>;
  output_a: Record<string, unknown> | null;
  output_b: Record<string, unknown> | null;
  score_a: string | number | null;
  score_b: string | number | null;
  label: string | number | null;
  disagree: boolean;
}

export interface CompareOut {
  run_a: Run;
  run_b: Run;
  metrics_delta: Record<string, number>;
  rows: CompareRow[];
}

// A normalized run progress event as delivered by `runs.streamEvents`. The
// discriminating `type` mirrors the SSE event name; the remaining fields are the
// event payload (e.g. `completed` on `status`, `total` on `started`).
export interface RunStreamEvent {
  type: "status" | "started" | "row" | "progress" | "finished" | "error";
  status?: RunStatus;
  completed?: number;
  total?: number;
  row_id?: string;
  success?: boolean;
  score_value?: string | number | null;
  metrics?: Record<string, unknown> | null;
  error?: string;
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

export interface ExportResponse {
  source: string;
}

// The new export endpoints return every emitted file keyed by filename, so a
// bundled export is a one-entry map and a split export carries both files.
export type ExportFilesResponse = { files: Record<string, string> };
// "code" emits the standalone Python module(s); "json" emits the config package.
export type ExportFormat = "code" | "json";
// "bundled" is one file; "split" hoists agent and dataset into separate files.
export type ExportLayout = "bundled" | "split";

export interface ErrorBody {
  type: string;
  message: string;
  detail?: Record<string, unknown> | null;
}

export interface DatasetUpdate {
  name?: string;
  description?: string;
  columns?: string[];
  column_renames?: Record<string, string>;
  label_schema?: LabelSchema;
  force?: boolean;
}

export interface EvaluatorUpdate {
  name?: string;
  description?: string;
}

// Seeded generation: derive a dataset's shape from an evaluator version. The
// version's `required_columns` are always included; `extra_columns` add explicit
// user-named columns and `column_notes`/`instructions`/`label_guidance` steer the
// generated content and (opt-in) suggested labels.
export interface DatasetGenerateFromVersion {
  version_id: string;
  name: string;
  description?: string;
  instructions?: string;
  extra_columns?: string[];
  column_notes?: Record<string, string>;
  include_labels?: boolean;
  label_guidance?: string;
  label_mix?: LabelMix;
  count: number;
}

// Label -> its share of the requested row count, summing to 1.0. Omit the field entirely
// to leave the distribution to the description/instructions; a categorical label space is
// required, and naming only some labels gives the rest no rows.
export type LabelMix = Record<string, number>;

// How a generated dataset's rows were asked for. Null for an uploaded or blank dataset,
// which was never generated. `source_version_id` is provenance only — the version it
// names may since have changed or been deleted.
export interface DatasetGeneration {
  count: number;
  instructions: string | null;
  column_notes: Record<string, string> | null;
  label_mix: LabelMix | null;
  label_guidance: string | null;
  include_labels: boolean;
  source_version_id: string | null;
}

// Generate more rows into an existing dataset. Every field but `count` is an override:
// omitted ones fall back to the dataset's stored generation settings. Shape is never
// overridable — the dataset's own columns and label space always apply.
export interface RowsGenerate {
  count: number;
  instructions?: string;
  column_notes?: Record<string, string>;
  label_mix?: LabelMix;
  label_guidance?: string;
}

// One credential the setup walkthrough checks for. `set` reflects effective
// presence (env or CLI-written config); `command` is the CLI invocation shown
// to the user when it is missing.
export interface SetupKey {
  name: "gateway_api_key" | "logfire_token" | "logfire_api_key";
  set: boolean;
  required: boolean;
  label: string;
  command: string;
  purpose: string;
}

export interface SetupStatus {
  keys: SetupKey[];
}
