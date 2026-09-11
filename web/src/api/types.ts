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
}

// The list response additionally carries each dataset's row and labeled-row counts,
// which the server can only fill from a grouped query across every dataset at once. A
// single-dataset fetch returns the plain `Dataset`; read those counts from
// `datasets.stats(id)` instead.
export interface DatasetSummary extends Dataset {
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
  dataset_id: string;
  idx: number;
  data: Record<string, unknown>;
}

export interface RowsPage {
  rows: DatasetRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface RowDataUpdate {
  data: Record<string, unknown>;
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

// One label within a label set: its name and the criteria description shown as
// reference text next to it in the annotation UI.
export interface AnnotationLabel {
  name: string;
  description: string;
}

export interface LabelSet {
  id: string;
  created_at: string;
  dataset_id: string;
  name: string;
  description: string;
  kind: ScoreKind;
  labels: AnnotationLabel[] | null;
  minimum: number | null;
  maximum: number | null;
}

// A label set as listed for a dataset, carrying its annotation progress.
export interface LabelSetProgress extends LabelSet {
  annotated_count: number;
  row_count: number;
}

export interface LabelSetCreate {
  name: string;
  description?: string;
  kind: ScoreKind;
  labels?: AnnotationLabel[] | null;
  minimum?: number | null;
  maximum?: number | null;
}

export interface LabelSetUpdate {
  name?: string;
  description?: string;
}

// A row's annotation under one label set. `source` is null until the row has ever
// been confirmed or accepted; `suggested_*` are populated only by generation.
export interface Annotation {
  id: string;
  label_set_id: string;
  dataset_row_id: string;
  labels: string[];
  value: number | null;
  suggested_labels: string[] | null;
  suggested_value: number | null;
  source: LabelSource | null;
  reasoning: string | null;
  description: string | null;
}

// Request body to create or replace a row's annotation. This is a full replace, not a
// patch: every field is written as sent, so a caller changing only one of
// labels/value/description must still send the row's current values for the others.
export interface AnnotationPut {
  labels?: string[];
  value?: number | null;
  description?: string | null;
}

export interface AnnotationRow {
  row_id: string;
  idx: number;
  data: Record<string, unknown>;
  annotation: Annotation | null;
}

export interface AnnotationRowsPage {
  rows: AnnotationRow[];
  total: number;
  annotated_count: number;
  limit: number;
  offset: number;
}

// Whether a VALIDATION run over this dataset/version pairing would have any ground
// truth to compare against, and how much. `label_set_id` is null when no label set on
// the dataset matches the version's score contract -- only EVAL can run then.
export interface RunCoverage {
  label_set_id: string | null;
  total_rows: number;
  labeled_rows: number;
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

export type SetupKeyName =
  | "gateway_api_key"
  | "logfire_token"
  | "logfire_read_key"
  | "logfire_write_key";

// One credential the setup walkthrough checks for. `set` reflects effective
// presence (env or CLI-written config); `command` is the CLI invocation shown
// to the user when it is missing. `explanation` is the longer Settings copy.
// Never carries the key value.
export interface SetupKey {
  name: SetupKeyName;
  set: boolean;
  required: boolean;
  label: string;
  command: string;
  purpose: string;
  explanation: string;
  from_env: boolean;
}

export type ClearName = SetupKeyName | "local_cli_default";

export interface SetupStatus {
  keys: SetupKey[];
  default_model: string;
  local_cli_default: string | null;
  local_cli_options: string[];
  // Not secrets: pages in the Logfire project the read key is scoped to. Null when
  // the key is unset or lookup failed. A stored Explore URL fills explore only.
  logfire_explore_url: string | null;
  logfire_traces_url: string | null;
  logfire_datasets_url: string | null;
}

export interface SetupKeysIn {
  gateway_api_key?: string;
  logfire_token?: string;
  logfire_read_key?: string;
  logfire_write_key?: string;
  local_cli_default?: string | null;
  clear?: ClearName[];
}

export interface DatasetLogfirePull {
  sql: string;
  sample_n: number;
  seed: number;
  min_timestamp: string | null;
  max_timestamp: string | null;
  label_column: string | null;
}

// Pull more rows from Logfire into an existing dataset. Every field is an override: omitted
// ones fall back to the dataset's stored pull settings. `seed` is never prefilled from storage
// (a fresh sample is the default); `label_column` is never overridable — shape stays fixed.
export interface RowsLogfirePull {
  sql?: string;
  sample_n?: number;
  seed?: number;
  min_timestamp?: string;
  max_timestamp?: string;
}

export interface DatasetHostedFetch {
  source_name: string;
}

export interface DatasetFromLogfire {
  name: string;
  description?: string;
  sql: string;
  sample_n: number;
  seed?: number;
  min_timestamp?: string;
  max_timestamp?: string;
  label_column?: string;
  label_schema?: LabelSchema;
}

export interface HostedDatasetSummary {
  id: string;
  name: string;
  description: string | null;
  case_count: number | null;
}

export interface DatasetFromLogfireHosted {
  source_name: string;
  name?: string;
  description?: string;
}

// What `POST /api/datasets/{id}/logfire/push` returns, mirroring the fields Logfire's
// DatasetDetail carries. Every field but `id` and `name` is NotRequired upstream, so the
// endpoint normalises absent ones to null rather than omitting them. There is no URL:
// Logfire's API does not return one, and valcore never constructs one.
export interface LogfirePushResult {
  id: string;
  name: string;
  case_count: number | null;
  output_schema: Record<string, unknown> | null;
}
