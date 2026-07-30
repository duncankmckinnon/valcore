// The single place the SPA talks to the API. Every request goes through `api<T>`,
// which parses the uniform `{error:{type,message}}` envelope and throws `ApiError`.

import type {
  Dataset,
  DatasetCreated,
  DatasetRow,
  DatasetStats,
  Evaluator,
  EvaluatorVersion,
  GeneratedConfig,
  LabelSchema,
  RefinedConfig,
  RowPatch,
  RowsPage,
  Run,
  RunResult,
} from "./types";

export class ApiError extends Error {
  type: string;
  status: number;

  constructor(message: string, type: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.type = type;
    this.status = status;
  }
}

async function parseError(response: Response): Promise<ApiError> {
  let type = "Error";
  let message = response.statusText || `Request failed with status ${response.status}`;
  try {
    const body = await response.json();
    if (body && typeof body === "object" && body.error) {
      type = body.error.type ?? type;
      message = body.error.message ?? message;
    }
  } catch {
    // Non-JSON error body (e.g. an HTML 500 page); fall back to the status text.
  }
  return new ApiError(message, type, response.status);
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
  remove: (id: string) => api<void>(`/api/evaluators/${id}`, { method: "DELETE" }),
  createVersion: (id: string, data: Partial<EvaluatorVersion>) =>
    api<EvaluatorVersion>(`/api/evaluators/${id}/versions`, { method: "POST", ...jsonBody(data) }),
  updateVersion: (id: string, versionId: string, data: Partial<EvaluatorVersion>) =>
    api<EvaluatorVersion>(`/api/evaluators/${id}/versions/${versionId}`, {
      method: "PATCH",
      ...jsonBody(data),
    }),
  copyVersion: (id: string, versionId: string) =>
    api<EvaluatorVersion>(`/api/evaluators/${id}/versions/${versionId}/copy`, { method: "POST" }),
  generate: (data: { criteria: string; columns?: string[]; model?: string }) =>
    api<GeneratedConfig>("/api/evaluators/generate", { method: "POST", ...jsonBody(data) }),
  refine: (data: { config: GeneratedConfig; instruction: string; model?: string }) =>
    api<RefinedConfig>("/api/evaluators/refine", { method: "POST", ...jsonBody(data) }),
  exportScript: (id: string, versionId: string) =>
    api<string>(`/api/evaluators/${id}/versions/${versionId}/export`),
};

export const datasets = {
  list: () => api<Dataset[]>("/api/datasets"),
  get: (id: string) => api<Dataset>(`/api/datasets/${id}`),
  create: (data: Partial<Dataset>) =>
    api<Dataset>("/api/datasets", { method: "POST", ...jsonBody(data) }),
  remove: (id: string) => api<void>(`/api/datasets/${id}`, { method: "DELETE" }),
  upload: (form: FormData) =>
    api<DatasetCreated>("/api/datasets/upload", { method: "POST", body: form }),
  generate: (data: {
    name: string;
    description?: string;
    columns: string[];
    label_schema: LabelSchema;
    count: number;
  }) => api<DatasetCreated>("/api/datasets/generate", { method: "POST", ...jsonBody(data) }),
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
  results: (id: string) => api<RunResult[]>(`/api/runs/${id}/results`),
  cancel: (id: string) => api<Run>(`/api/runs/${id}/cancel`, { method: "POST" }),
  retryFailed: (id: string) => api<Run>(`/api/runs/${id}/retry-failed`, { method: "POST" }),
  compare: (a: string, b: string) =>
    api<Record<string, unknown>>(`/api/runs/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`),
  streamEvents: (runId: string, onEvent: (event: Record<string, unknown>) => void): (() => void) => {
    const source = new EventSource(`/api/runs/${runId}/events`);
    source.onmessage = (message) => {
      try {
        onEvent(JSON.parse(message.data));
      } catch {
        // Ignore malformed SSE payloads rather than tearing down the stream.
      }
    };
    return () => source.close();
  },
};
