import type { FrontendTelemetryConfig } from "./api/types";
import type { FrontendConfigOptions } from "@pydantic/logfire-browser";
import { setup } from "./api/client";

interface FrontendTelemetryDependencies {
  loadBrowser: () => Promise<Pick<typeof import("@pydantic/logfire-browser"), "configureFrontend">>;
  loadReplayIntegration: () => Promise<
    Pick<typeof import("@pydantic/logfire-session-replay/integration"), "sessionReplayIntegration">
  >;
}

interface FrontendTelemetryStartupDependencies extends FrontendTelemetryDependencies {
  fetchConfig: () => Promise<FrontendTelemetryConfig>;
}

const defaultDependencies: FrontendTelemetryDependencies = {
  loadBrowser: () => import("@pydantic/logfire-browser"),
  loadReplayIntegration: () => import("@pydantic/logfire-session-replay/integration"),
};

const defaultStartupDependencies: FrontendTelemetryStartupDependencies = {
  ...defaultDependencies,
  fetchConfig: setup.frontendTelemetry,
};

function frontendBaseUrl(traceUrl: string): string {
  const url = new URL(traceUrl);
  const logfireHost =
    url.hostname === "logfire.pydantic.dev" ||
    (url.hostname.startsWith("logfire-") && url.hostname.endsWith(".pydantic.dev"));
  if (
    url.protocol !== "https:" ||
    !logfireHost ||
    url.port !== "" ||
    url.username !== "" ||
    url.password !== "" ||
    url.pathname !== "/v1/traces" ||
    url.search !== "" ||
    url.hash !== ""
  ) {
    throw new Error("Invalid Logfire frontend trace URL");
  }
  return url.origin;
}

export async function configureFrontendTelemetry(
  config: FrontendTelemetryConfig,
  dependencies: FrontendTelemetryDependencies = defaultDependencies,
): Promise<boolean> {
  if (!config.enabled || config.trace_url === null || config.token === null) return false;

  const baseUrl = frontendBaseUrl(config.trace_url);
  const browser = await dependencies.loadBrowser();
  const options: FrontendConfigOptions = {
    baseUrl,
    token: config.token,
  };

  if (config.session_replay) {
    const { sessionReplayIntegration } = await dependencies.loadReplayIntegration();
    options.sessionReplay = {
      ...sessionReplayIntegration({
        maskAllText: true,
        maskAllInputs: true,
        blockSelector:
          "[data-logfire-block], [data-row-id], [data-label], [data-actual], [data-predicted], [title], [aria-label]",
        captureConsole: false,
      }),
    };
  }

  browser.configureFrontend(options);
  return true;
}

export async function initializeFrontendTelemetry(
  dependencies: FrontendTelemetryStartupDependencies = defaultStartupDependencies,
): Promise<boolean> {
  try {
    const config = await dependencies.fetchConfig();
    return await configureFrontendTelemetry(config, dependencies);
  } catch {
    return false;
  }
}
