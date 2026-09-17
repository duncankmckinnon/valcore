import { describe, expect, it, vi } from "vitest";
import * as telemetry from "./frontendTelemetry";

describe("configureFrontendTelemetry", () => {
  it("configures browser tracing and privacy-safe session replay from runtime settings", async () => {
    const configureFrontend = vi.fn();
    const replayLoad = vi.fn();
    const sessionReplayIntegration = vi.fn().mockReturnValue({ load: replayLoad });

    const configured = await telemetry.configureFrontendTelemetry(
      {
        enabled: true,
        trace_url: "https://logfire-us.pydantic.dev/v1/traces",
        token: "lf-frontend-public",
        session_replay: true,
      },
      {
        loadBrowser: async () => ({ configureFrontend }),
        loadReplayIntegration: async () => ({ sessionReplayIntegration }),
      },
    );

    expect(configured).toBe(true);
    expect(sessionReplayIntegration).toHaveBeenCalledWith({
      maskAllText: true,
      maskAllInputs: true,
      blockSelector:
        "[data-logfire-block], [data-row-id], [data-label], [data-actual], [data-predicted], [title], [aria-label]",
      captureConsole: false,
    });
    expect(configureFrontend).toHaveBeenCalledOnce();
    const options = configureFrontend.mock.calls[0][0];
    expect(options).toMatchObject({
      baseUrl: "https://logfire-us.pydantic.dev",
      token: "lf-frontend-public",
      sessionReplay: { load: replayLoad },
    });
  });

  it("does not load the replay recorder when replay is disabled", async () => {
    const configureFrontend = vi.fn();
    const loadReplayIntegration = vi.fn();

    await telemetry.configureFrontendTelemetry(
      {
        enabled: true,
        trace_url: "https://logfire-us.pydantic.dev/v1/traces",
        token: "lf-frontend-public",
        session_replay: false,
      },
      {
        loadBrowser: async () => ({ configureFrontend }),
        loadReplayIntegration,
      },
    );

    expect(loadReplayIntegration).not.toHaveBeenCalled();
    expect(configureFrontend).toHaveBeenCalledWith({
      baseUrl: "https://logfire-us.pydantic.dev",
      token: "lf-frontend-public",
    });
  });

  it("fails open when runtime configuration cannot be loaded", async () => {
    await expect(
      telemetry.initializeFrontendTelemetry({
        fetchConfig: async () => {
          throw new Error("setup unavailable");
        },
        loadBrowser: async () => ({ configureFrontend: vi.fn() }),
        loadReplayIntegration: async () => ({ sessionReplayIntegration: vi.fn() }),
      }),
    ).resolves.toBe(false);
  });
});
