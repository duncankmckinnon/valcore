// The single place the SPA talks to the API. Every request goes through `api<T>`,
// which parses the uniform `{error:{type,message}}` envelope and throws `ApiError`.

import type {
  Dataset,
  DatasetCreated,
  DatasetGenerateFromVersion,
  DatasetGeneration,
  DatasetRow,
  DatasetStats,
  DatasetUpdate,
  Evaluator,
  EvaluatorUpdate,
  EvaluatorVersion,
  ExportFilesResponse,
  ExportFormat,
  ExportLayout,
  ExportResponse,
  GeneratedConfig,
  LabelMix,
  LabelSchema,
  Overview,
  RefinedConfig,
  RowPatch,
  RowsGenerate,
  RowsPage,
  CompareOut,
  ResultsPage,
  Run,
  RunStreamEvent,
} from "./types";

export class ApiError extends Error {
  type: string;
  status: number;
  detail: Record<string, unknown> | null;

  constructor(
    message: string,
    type: string,
    status: number,
    detail: Record<string, unknown> | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.type = type;
    this.status = status;
    this.detail = detail;
  }
}

async function parseError(response: Response): Promise<ApiError> {
  let type = "Error";
  let message = response.statusText || `Request failed with status ${response.status}`;
  let detail: Record<string, unknown> | null = null;
  try {
    const body = await response.json();
    if (body && typeof body === "object" && body.error) {
      type = body.error.type ?? type;
      message = body.error.message ?? message;
      detail = body.error.detail ?? null;
    }
  } catch {
    // Non-JSON error body (e.g. an HTML 500 page); fall back to the status text.
  }
  return new ApiError(message, type, response.status, detail);
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body !== undefined && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    throw await parseError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  const contentType = response.headers.get("Content-Type") ?? "";
  if (contentType.includes("application/json")) {
    return (await response.json()) as T;
  }
  return (await response.text()) as T;
}

function jsonBody(data: unknown): RequestInit {
  return { body: JSON.stringify(data) };
}

export const evaluators = {
  list: () => api<Evaluator[]>("/api/evaluators"),
  get: (id: string) => api<Evaluator>(`/api/evaluators/${id}`),
  create: (data: { name: string; description?: string }) =>
    api<Evaluator>("/api/evaluators", { method: "POST", ...jsonBody(data) }),
  update: (id: string, data: EvaluatorUpdate) =>
    api<Evaluator>(`/api/evaluators/${id}`, { method: "PATCH", ...jsonBody(data) }),
  remove: (id: string) => api<void>(`/api/evaluators/${id}`, { method: "DELETE" }),
  createVersion: (id: string, data: Partial<EvaluatorVersion>) =>
    api<EvaluatorVersion>(`/api/evaluators/${id}/versions`, { method: "POST", ...jsonBody(data) }),
  // The version routes are keyed by version id alone; there is no evaluator-id
  // segment. `id` is kept only so existing callers keep their arity.
  updateVersion: (_id: string, versionId: string, data: Partial<EvaluatorVersion>) =>
    api<EvaluatorVersion>(`/api/evaluators/versions/${versionId}`, {
      method: "PATCH",
      ...jsonBody(data),
    }),
  copyVersion: (_id: string, versionId: string) =>
    api<EvaluatorVersion>(`/api/evaluators/versions/${versionId}/copy`, { method: "POST" }),
  deleteVersion: (_id: string, versionId: string) =>
    api<void>(`/api/evaluators/versions/${versionId}`, { method: "DELETE" }),
  generate: (data: {
    criteria: string;
    columns?: string[];
    model?: string;
    dataset_id?: string;
    column_notes?: Record<string, string>;
  }) => api<GeneratedConfig>("/api/evaluators/generate", { method: "POST", ...jsonBody(data) }),
  refine: (data: { config: GeneratedConfig; instruction: string; model?: string }) =>
    api<RefinedConfig>("/api/evaluators/refine", { method: "POST", ...jsonBody(data) }),
  exportScript: async (_id: string, versionId: string) => {
    const response = await api<ExportResponse>(
      `/api/evaluators/versions/${versionId}/export`,
    );
    return response.source;
  },
  // "code" is the single-file Python script and carries no layout param; only the
  // JSON package distinguishes bundled from split via `split`.
  exportFiles: async (versionId: string, format: ExportFormat, layout: ExportLayout) => {
    const base = `/api/evaluators/versions/${versionId}`;
    const path =
      format === "code"
        ? `${base}/export.py`
        : `${base}/export.json?split=${layout === "split" ? "true" : "false"}`;
    const response = await api<ExportFilesResponse>(path);
    return response.files;
  },
};

export const datasets = {
  list: () => api<Dataset[]>("/api/datasets"),
  get: (id: string) => api<Dataset>(`/api/datasets/${id}`),
  create: (data: Partial<Dataset>) =>
    api<Dataset>("/api/datasets", { method: "POST", ...jsonBody(data) }),
  update: (id: string, data: DatasetUpdate) =>
    api<Dataset>(`/api/datasets/${id}`, { method: "PATCH", ...jsonBody(data) }),
  remove: (id: string) => api<void>(`/api/datasets/${id}`, { method: "DELETE" }),
  addRows: (id: string, rows: Record<string, unknown>[]) =>
    api<DatasetRow[]>(`/api/datasets/${id}/rows`, { method: "POST", ...jsonBody({ rows }) }),
  deleteRow: (rowId: string) =>
    api<void>(`/api/datasets/rows/${rowId}`, { method: "DELETE" }),
  upload: (form: FormData) =>
    api<DatasetCreated>("/api/datasets/upload", { method: "POST", body: form }),
  generate: (data: {
    name: string;
    description?: string;
    columns: string[];
    column_notes?: Record<string, string>;
    label_schema: LabelSchema;
    instructions?: string;
    label_mix?: LabelMix;
    count: number;
  }) => api<DatasetCreated>("/api/datasets/generate", { method: "POST", ...jsonBody(data) }),
  generateFromVersion: (data: DatasetGenerateFromVersion) =>
    api<DatasetCreated>("/api/datasets/generate-from-version", {
      method: "POST",
      ...jsonBody(data),
    }),
  // Null for a dataset that was uploaded or created blank rather than generated.
  generation: (id: string) =>
    api<DatasetGeneration | null>(`/api/datasets/${id}/generation`),
  generateRows: (id: string, data: RowsGenerate) =>
    api<DatasetRow[]>(`/api/datasets/${id}/generate-rows`, {
      method: "POST",
      ...jsonBody(data),
    }),
  rows: (id: string, params?: { limit?: number; offset?: number }) => {
    const query = new URLSearchParams();
    if (params?.limit !== undefined) query.set("limit", String(params.limit));
    if (params?.offset !== undefined) query.set("offset", String(params.offset));
    const suffix = query.toString();
    return api<RowsPage>(`/api/datasets/${id}/rows${suffix ? `?${suffix}` : ""}`);
  },
  // The API patches rows by row id alone; there is no dataset-id path segment.
  patchRow: (rowId: string, data: RowPatch) =>
    api<DatasetRow>(`/api/datasets/rows/${rowId}`, { method: "PATCH", ...jsonBody(data) }),
  stats: (id: string) => api<DatasetStats>(`/api/datasets/${id}/stats`),
  // "code" emits the Python module with no query params; "json" threads an optional
  // `version_id` (omitted entirely when absent) and the `split` layout flag.
  exportFiles: async (
    id: string,
    format: ExportFormat,
    opts: { versionId?: string; layout: ExportLayout },
  ) => {
    let path = `/api/datasets/${id}/export.py`;
    if (format === "json") {
      const query = new URLSearchParams();
      if (opts.versionId !== undefined) query.set("version_id", opts.versionId);
      query.set("split", opts.layout === "split" ? "true" : "false");
      path = `/api/datasets/${id}/export.json?${query.toString()}`;
    }
    const response = await api<ExportFilesResponse>(path);
    return response.files;
  },
};

export const overview = {
  get: () => api<Overview>("/api/overview"),
};

export const runs = {
  list: () => api<Run[]>("/api/runs"),
  get: (id: string) => api<Run>(`/api/runs/${id}`),
  create: (data: {
    kind: string;
    version_id: string;
    dataset_id: string;
    concurrency?: number;
  }) => api<Run>("/api/runs", { method: "POST", ...jsonBody(data) }),
  results: (
    id: string,
    params?: { only_disagreements?: boolean; only_errors?: boolean; limit?: number; offset?: number },
  ) => {
    const query = new URLSearchParams();
    if (params?.only_disagreements) query.set("only_disagreements", "true");
    if (params?.only_errors) query.set("only_errors", "true");
    if (params?.limit !== undefined) query.set("limit", String(params.limit));
    if (params?.offset !== undefined) query.set("offset", String(params.offset));
    const suffix = query.toString();
    return api<ResultsPage>(`/api/runs/${id}/results${suffix ? `?${suffix}` : ""}`);
  },
  cancel: (id: string) => api<Run>(`/api/runs/${id}/cancel`, { method: "POST" }),
  retryFailed: (id: string) => api<Run>(`/api/runs/${id}/retry-failed`, { method: "POST" }),
  compare: (a: string, b: string) =>
    api<CompareOut>(`/api/runs/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`),
  // The API streams *named* SSE events (`status`, `started`, `row`, `finished`, ...),
  // so we attach a listener per name and fold the event name into the payload as
  // `type`. `onmessage` alone would silently miss every named event.
  streamEvents: (runId: string, onEvent: (event: RunStreamEvent) => void): (() => void) => {
    const source = new EventSource(`/api/runs/${runId}/events`);
    const types: RunStreamEvent["type"][] = [
      "status",
      "started",
      "row",
      "progress",
      "finished",
      "error",
    ];
    for (const type of types) {
      source.addEventListener(type, (message) => {
        try {
          const payload = JSON.parse((message as MessageEvent).data);
          onEvent({ type, ...payload });
        } catch {
          // Ignore malformed SSE payloads rather than tearing down the stream.
        }
      });
    }
    return () => source.close();
  },
};
