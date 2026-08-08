import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, datasets, evaluators, overview } from "./client";
import type { Dataset, Overview } from "./types";

function jsonResponse(body: unknown, init: { status?: number } = {}): Response {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { "Content-Type": "application/json" },
  });
}

function noContentResponse(): Response {
  return new Response(null, { status: 204 });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("api", () => {
  it("returns parsed JSON on a 200 response", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ id: "e1", name: "Judge" }));

    const result = await api<{ id: string; name: string }>("/api/evaluators/e1");

    expect(result).toEqual({ id: "e1", name: "Judge" });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("throws ApiError carrying type, status, and message on an error body", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(
        { error: { type: "ConfigError", message: "score_field is invalid" } },
        { status: 422 },
      ),
    );

    const error = await api("/api/evaluators").catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) throw error;
    expect(error.type).toBe("ConfigError");
    expect(error.status).toBe(422);
    expect(error.message).toBe("score_field is invalid");
  });

  it("handles a non-JSON error body without crashing", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("<html>Internal Server Error</html>", {
        status: 500,
        headers: { "Content-Type": "text/html" },
      }),
    );

    const error = await api("/api/runs").catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) throw error;
    expect(error.status).toBe(500);
    expect(typeof error.message).toBe("string");
    expect(error.message.length).toBeGreaterThan(0);
  });
});

describe("resource helpers", () => {
  it("evaluators.create POSTs JSON to /api/evaluators", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ id: "e1" }));

    await evaluators.create({ name: "Judge" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/evaluators");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ name: "Judge" }));
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
  });

  it("datasets.patchRow PATCHes the row URL", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ id: "r1" }));

    await datasets.patchRow("r1", { note: "off" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/datasets/rows/r1");
    expect(init?.method).toBe("PATCH");
  });
});

// The version routes have no evaluator-id segment; these three currently build a
// wrong path and 404 at runtime. Each assertion must fail against the pre-fix code.
describe("evaluator version path bug", () => {
  it("updateVersion PATCHes /api/evaluators/versions/{vid} with no evaluator id", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ id: "v1" }));

    await evaluators.updateVersion("ev1", "v1", { notes: "tweak" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/evaluators/versions/v1");
    expect(init?.method).toBe("PATCH");
  });

  it("copyVersion POSTs /api/evaluators/versions/{vid}/copy with no evaluator id", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ id: "v2" }));

    await evaluators.copyVersion("ev1", "v1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/evaluators/versions/v1/copy");
    expect(init?.method).toBe("POST");
  });

  it("exportScript GETs /api/evaluators/versions/{vid}/export with no evaluator id", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ source: "def evaluate(): ..." }));

    await evaluators.exportScript("ev1", "v1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/evaluators/versions/v1/export");
    expect(init?.method ?? "GET").toBe("GET");
  });

  it("exportScript resolves to the source string inside the response envelope", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ source: "def evaluate(): ..." }),
    );

    const source = await evaluators.exportScript("ev1", "v1");

    expect(source).toBe("def evaluate(): ...");
  });
});

// The new export endpoints return a `{files: {name -> contents}}` envelope for
// both entities and both forms. `exportFiles` unwraps to the inner map so callers
// never see the envelope, mirroring how `exportScript` returns just `.source`.
// `split` is a JSON-only query param ("true" for the split layout, "false" for
// bundled); the "code" form never sends it. The dataset variant threads an
// optional `versionId` into `version_id`, omitting the param entirely when absent.
describe("export files client helpers", () => {
  it("evaluators.exportFiles code GETs export.py with no split param", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ files: { "refusal.py": "def evaluate(): ..." } }));

    await evaluators.exportFiles("v1", "code", "bundled");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/evaluators/versions/v1/export.py");
    expect(init?.method ?? "GET").toBe("GET");
    // The code form is a single-file Python script; layout must not leak a query param.
    expect(String(url)).not.toContain("split");
  });

  it("evaluators.exportFiles json bundled GETs export.json with split=false", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ files: { "refusal.json": "{}" } }));

    await evaluators.exportFiles("v1", "json", "bundled");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/evaluators/versions/v1/export.json?split=false");
    expect(init?.method ?? "GET").toBe("GET");
  });

  it("evaluators.exportFiles json split GETs export.json with split=true", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ files: { "refusal.agent.json": "{}" } }));

    await evaluators.exportFiles("v1", "json", "split");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/evaluators/versions/v1/export.json?split=true");
  });

  it("evaluators.exportFiles resolves to the inner files map", async () => {
    const files = { "refusal.agent.json": "{}", "refusal.dataset.json": "{}" };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ files }));

    const result = await evaluators.exportFiles("v1", "json", "split");

    expect(result).toEqual(files);
  });

  it("evaluators.exportFiles surfaces a non-OK response as a rejection", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ error: { type: "NotFoundError", message: "no such version" } }, { status: 404 }),
    );

    const error = await evaluators.exportFiles("missing", "json", "bundled").catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) throw error;
    expect(error.status).toBe(404);
    expect(error.type).toBe("NotFoundError");
  });

  it("datasets.exportFiles code GETs export.py with no query params", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ files: { "refusal_dataset.py": "# ..." } }));

    await datasets.exportFiles("d1", "code", { layout: "bundled" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/datasets/d1/export.py");
    expect(init?.method ?? "GET").toBe("GET");
    expect(String(url)).not.toContain("split");
    expect(String(url)).not.toContain("version_id");
  });

  it("datasets.exportFiles json includes version_id when given", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ files: { "refusal.json": "{}" } }));

    await datasets.exportFiles("d1", "json", { versionId: "v1", layout: "bundled" });

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/datasets/d1/export.json?version_id=v1&split=false");
  });

  it("datasets.exportFiles json omits version_id entirely when not supplied", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ files: { "refusal.json": "{}" } }));

    await datasets.exportFiles("d1", "json", { layout: "split" });

    const [url] = fetchMock.mock.calls[0];
    // Absent optional params are dropped, never string-concatenated as `version_id=undefined`.
    expect(url).toBe("/api/datasets/d1/export.json?split=true");
    expect(String(url)).not.toContain("version_id");
  });

  it("datasets.exportFiles resolves to the inner files map", async () => {
    const files = { "refusal.dataset.json": "{}" };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ files }));

    const result = await datasets.exportFiles("d1", "json", { layout: "bundled" });

    expect(result).toEqual(files);
  });

  it("datasets.exportFiles surfaces a non-OK response as a rejection", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ error: { type: "NotFoundError", message: "no such dataset" } }, { status: 404 }),
    );

    const error = await datasets.exportFiles("missing", "json", { layout: "bundled" }).catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) throw error;
    expect(error.status).toBe(404);
  });
});

describe("manual CRUD helpers", () => {
  it("evaluators.update PATCHes /api/evaluators/{id}", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ id: "ev1", name: "Renamed" }));

    await evaluators.update("ev1", { name: "Renamed" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/evaluators/ev1");
    expect(init?.method).toBe("PATCH");
    expect(init?.body).toBe(JSON.stringify({ name: "Renamed" }));
  });

  it("evaluators.deleteVersion DELETEs /api/evaluators/versions/{vid}", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(noContentResponse());

    await evaluators.deleteVersion("ev1", "v1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/evaluators/versions/v1");
    expect(init?.method).toBe("DELETE");
  });

  it("datasets.update PATCHes /api/datasets/{id}", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ id: "d1", name: "Renamed" }));

    await datasets.update("d1", { name: "Renamed", force: true });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/datasets/d1");
    expect(init?.method).toBe("PATCH");
    expect(init?.body).toBe(JSON.stringify({ name: "Renamed", force: true }));
  });

  it("datasets.addRows POSTs {rows} to /api/datasets/{id}/rows and returns the rows", async () => {
    const created = [{ id: "r1" }, { id: "r2" }];
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(created));

    const rows = [{ text: "a" }, { text: "b" }];
    const result = await datasets.addRows("d1", rows);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/datasets/d1/rows");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ rows }));
    expect(result).toEqual(created);
  });

  it("datasets.deleteRow DELETEs /api/datasets/rows/{rowId}", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(noContentResponse());

    await datasets.deleteRow("r1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/datasets/rows/r1");
    expect(init?.method).toBe("DELETE");
  });
});

// Seeded generation: a dataset's shape can be derived from an evaluator version,
// and an evaluator's columns from a dataset. Only content-steering fields are
// optional; the shape always comes from the source, so the client just forwards
// whatever the caller supplies.
describe("seeded generation client helpers", () => {
  it("generateFromVersion POSTs the full body to /api/datasets/generate-from-version", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ dataset: { id: "d1" }, row_count: 5 }));

    const payload = {
      version_id: "v1",
      name: "Seeded from v1",
      description: "test data",
      instructions: "make it varied",
      extra_columns: ["notes"],
      column_notes: { input: "keep it short" },
      include_labels: true,
      label_guidance: "assign the strictest applicable label",
      count: 5,
    };
    await datasets.generateFromVersion(payload);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/datasets/generate-from-version");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify(payload));
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
  });

  it("generateFromVersion omits optional fields left unset from the body", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ dataset: { id: "d1" }, row_count: 3 }));

    const payload = { version_id: "v1", name: "Minimal", count: 3 };
    await datasets.generateFromVersion(payload);

    const [, init] = fetchMock.mock.calls[0];
    // JSON.stringify drops undefined keys, so the wire body must contain only the
    // three fields the caller actually supplied.
    expect(init?.body).toBe(JSON.stringify(payload));
    const parsed = JSON.parse(init?.body as string);
    expect(Object.keys(parsed).sort()).toEqual(["count", "name", "version_id"]);
  });

  it("generateFromVersion returns the DatasetCreated envelope", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ dataset: { id: "d1", name: "Seeded" }, row_count: 5 }),
    );

    const result = await datasets.generateFromVersion({
      version_id: "v1",
      name: "Seeded",
      count: 5,
    });

    expect(result).toEqual({ dataset: { id: "d1", name: "Seeded" }, row_count: 5 });
  });

  it("datasets.generate still POSTs to /api/datasets/generate and forwards instructions", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ dataset: { id: "d1" }, row_count: 4 }));

    const payload = {
      name: "Blank seeded",
      columns: ["input", "output"],
      label_schema: { kind: "categorical" as const, labels: ["pass", "fail"], minimum: null, maximum: null },
      instructions: "vary the tone",
      count: 4,
    };
    await datasets.generate(payload);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/datasets/generate");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify(payload));
    expect(JSON.parse(init?.body as string).instructions).toBe("vary the tone");
  });

  it("evaluators.generate forwards dataset_id and column_notes to /api/evaluators/generate", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ name: "Judge", required_columns: ["input"] }));

    const payload = {
      criteria: "score for helpfulness",
      dataset_id: "d1",
      column_notes: { input: "the user prompt" },
    };
    await evaluators.generate(payload);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/evaluators/generate");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify(payload));
    const parsed = JSON.parse(init?.body as string);
    expect(parsed.dataset_id).toBe("d1");
    expect(parsed.column_notes).toEqual({ input: "the user prompt" });
  });
});

describe("ApiError detail", () => {
  it("carries error.detail from a 409 ReferencedError body", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(
        { error: { type: "ReferencedError", message: "m", detail: { run_count: 4 } } },
        { status: 409 },
      ),
    );

    const error = await api("/api/evaluators/ev1").catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) throw error;
    expect(error.type).toBe("ReferencedError");
    expect(error.status).toBe(409);
    expect(error.detail?.run_count).toBe(4);
  });

  it("leaves detail null when the error body has no detail", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(
        { error: { type: "ConfigError", message: "score_field is invalid" } },
        { status: 422 },
      ),
    );

    const error = await api("/api/evaluators").catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) throw error;
    expect(error.detail).toBeNull();
  });
});

// The Overview endpoint is read-only and drives the new landing page. It follows the
// same GET-and-parse shape as the other resource getters, so it must both hit the
// right path and surface an ApiError on a non-OK response.
describe("overview client helper", () => {
  it("overview.get GETs /api/overview and returns the parsed body", async () => {
    const body: Overview = {
      evaluator_count: 3,
      dataset_count: 2,
      run_count: 5,
      total_rows: 120,
      labeled_rows: 80,
      best_accuracy: 0.92,
      latest_run: {
        id: "run1",
        dataset_name: "Support tickets",
        status: "completed",
        accuracy: 0.88,
        finished_at: "2026-08-07T12:00:00Z",
      },
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(body));

    const result = await overview.get();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/overview");
    // No method means the default GET; overview is strictly read-only.
    expect(init?.method ?? "GET").toBe("GET");
    expect(result).toEqual(body);
  });

  it("overview.get tolerates a null latest_run and null best_accuracy on an empty workspace", async () => {
    const body: Overview = {
      evaluator_count: 0,
      dataset_count: 0,
      run_count: 0,
      total_rows: 0,
      labeled_rows: 0,
      best_accuracy: null,
      latest_run: null,
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(body));

    const result = await overview.get();

    expect(result.latest_run).toBeNull();
    expect(result.best_accuracy).toBeNull();
  });

  it("overview.get surfaces an ApiError on a non-OK response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(
        { error: { type: "Error", message: "overview unavailable" } },
        { status: 500 },
      ),
    );

    const error = await overview.get().catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) throw error;
    expect(error.status).toBe(500);
    expect(error.message).toBe("overview unavailable");
  });
});

// The dataset list items now carry row and label counts. This exercises that the
// Dataset type accepts the two new required fields and that a list response round-trips
// them unchanged.
describe("dataset count fields", () => {
  it("datasets.list round-trips row_count and labeled_count on each item", async () => {
    const items: Dataset[] = [
      {
        id: "d1",
        created_at: "2026-08-07T00:00:00Z",
        name: "Support tickets",
        description: "",
        columns: ["input", "output"],
        label_schema: { kind: "categorical", labels: ["pass", "fail"], minimum: null, maximum: null },
        row_count: 42,
        labeled_count: 30,
      },
    ];
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(items));

    const result = await datasets.list();

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/datasets");
    expect(result).toEqual(items);
    expect(result[0].row_count).toBe(42);
    expect(result[0].labeled_count).toBe(30);
  });
});
