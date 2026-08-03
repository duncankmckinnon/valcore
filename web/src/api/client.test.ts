import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, datasets, evaluators } from "./client";

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
