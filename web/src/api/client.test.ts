import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, datasets, evaluators } from "./client";

function jsonResponse(body: unknown, init: { status?: number } = {}): Response {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { "Content-Type": "application/json" },
  });
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

    await datasets.patchRow("d1", "r1", { note: "off" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/datasets/d1/rows/r1");
    expect(init?.method).toBe("PATCH");
  });
});
