import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import OverviewPage from "./OverviewPage";
import { overview, setup } from "../api/client";
import type { Overview, SetupKey, SetupStatus } from "../api/types";

// The overview and setup endpoints are exercised here; the page makes one request to each on
// mount. Keep every other client member intact so the module loads.
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    overview: { ...actual.overview, get: vi.fn() },
    setup: { ...actual.setup, get: vi.fn() },
  };
});

const getMock = vi.mocked(overview.get);
const setupGet = vi.mocked(setup.get);

function makeOverview(overrides: Partial<Overview> = {}): Overview {
  return {
    evaluator_count: 7,
    dataset_count: 12,
    run_count: 4,
    total_rows: 40,
    labeled_rows: 5,
    best_accuracy: 0.91,
    latest_run: {
      id: "run-9",
      dataset_name: "Support tickets",
      status: "completed",
      accuracy: 0.87,
      finished_at: "2026-08-06T12:00:00Z",
    },
    ...overrides,
  };
}

// Builds a SetupStatus with all three known keys, overriding only the `set` flag for each so a
// test can flip just the one bit it cares about. Mirrors the helper in useSetup.test.tsx, but
// keeps its own copy of the per-key label/command/purpose so this suite can assert on them
// without importing test fixtures across modules.
function makeSetupStatus(overrides: Partial<Record<SetupKey["name"], boolean>> = {}): SetupStatus {
  const defaults: Record<SetupKey["name"], boolean> = {
    gateway_api_key: true,
    logfire_token: true,
    logfire_api_key: true,
  };
  const set = { ...defaults, ...overrides };
  const fixed: Record<SetupKey["name"], Omit<SetupKey, "set">> = {
    gateway_api_key: {
      name: "gateway_api_key",
      required: true,
      label: "Pydantic AI Gateway key",
      command: "valcore config set gateway_api_key <key>",
      purpose: "Required to generate datasets and run evaluators.",
    },
    logfire_token: {
      name: "logfire_token",
      required: false,
      label: "Logfire write token",
      command: "valcore config set logfire_token <token>",
      purpose: "Sends run and row spans to Logfire for tracing.",
    },
    logfire_api_key: {
      name: "logfire_api_key",
      required: false,
      label: "Logfire API key",
      command: "valcore config set logfire_api_key <key>",
      purpose: "Pushes datasets to Logfire's hosted dataset store.",
    },
  };
  const names: SetupKey["name"][] = ["gateway_api_key", "logfire_token", "logfire_api_key"];
  return { keys: names.map((name) => ({ ...fixed[name], set: set[name] })) };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <OverviewPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  // A harmless default (all keys set, card collapsed) so every pre-existing test that doesn't
  // care about setup state still gets a resolved promise instead of an unhandled rejection.
  setupGet.mockResolvedValue(makeSetupStatus());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("OverviewPage", () => {
  it("shows a spinner while the request is in flight", () => {
    // A promise that never settles keeps the page in its loading state.
    getMock.mockReturnValue(new Promise<Overview>(() => {}));

    renderPage();

    expect(screen.getByRole("status", { name: "Loading" })).toBeTruthy();
    expect(screen.queryByText("Best accuracy")).toBeNull();
  });

  it("renders all three stat cards with their values once loaded", async () => {
    getMock.mockResolvedValue(makeOverview());

    renderPage();

    // A PageHeader titled "Overview" describes what valcore does.
    expect(await screen.findByText("Overview")).toBeTruthy();

    expect(screen.getByText("Evaluators")).toBeTruthy();
    expect(screen.getByText("7")).toBeTruthy();

    expect(screen.getByText("Datasets")).toBeTruthy();
    expect(screen.getByText("12")).toBeTruthy();
    // labeled_rows of total_rows — 5 labeled of the 40 total dataset rows.
    expect(screen.getByText(/5 of 40 labeled/i)).toBeTruthy();

    expect(screen.getByText("Best accuracy")).toBeTruthy();
    expect(screen.getByText("91%")).toBeTruthy();
  });

  it("formats best_accuracy 0.91 as 91%", async () => {
    getMock.mockResolvedValue(makeOverview({ best_accuracy: 0.91 }));

    renderPage();

    expect(await screen.findByText("91%")).toBeTruthy();
  });

  it("renders an em dash for a null best_accuracy and never NaN% or 0%", async () => {
    getMock.mockResolvedValue(makeOverview({ best_accuracy: null }));

    const { container } = renderPage();

    await screen.findByText("Best accuracy");

    expect(screen.getByText("—")).toBeTruthy();
    expect(container.textContent).not.toContain("NaN");
    expect(container.textContent).not.toContain("0%");
  });

  it("guards the latest_run accuracy too: null renders an em dash, never NaN% or 0%", async () => {
    // The run card is present (latest_run is not null) but its accuracy is not yet
    // measured. best_accuracy is a whole number here so the only em dash on the page
    // must come from the run card's null accuracy.
    // best_accuracy 0.53 -> "53%", chosen so its own rendering never contains the
    // "0%" substring the guard below looks for; the only em dash comes from the run.
    getMock.mockResolvedValue(
      makeOverview({
        best_accuracy: 0.53,
        latest_run: {
          id: "run-9",
          dataset_name: "Support tickets",
          status: "running",
          accuracy: null,
          finished_at: null,
        },
      }),
    );

    const { container } = renderPage();

    await screen.findByText("Support tickets");

    // In the run card the em dash sits alongside the status ("— · running"), so it
    // is not an isolated element — assert on the composed text instead.
    expect(container.textContent).toContain("—");
    expect(container.textContent).not.toContain("NaN");
    expect(container.textContent).not.toContain("0%");
  });

  it("rounds fractional accuracy to a whole percentage", async () => {
    // 0.876 * 100 = 87.6, which must round to 88 — not truncate to 87.
    getMock.mockResolvedValue(makeOverview({ best_accuracy: 0.876 }));

    renderPage();

    expect(await screen.findByText("88%")).toBeTruthy();
  });

  it("replaces the run card with a create-dataset prompt when latest_run is null", async () => {
    getMock.mockResolvedValue(makeOverview({ latest_run: null }));

    renderPage();

    await screen.findByText("Best accuracy");

    const links = screen.getAllByRole("link");
    // The prompt links the user to /datasets to create their first dataset...
    expect(links.some((a) => a.getAttribute("href") === "/datasets")).toBe(true);
    // ...and no run card is rendered, so nothing links to a run detail page.
    expect(links.some((a) => a.getAttribute("href")?.startsWith("/runs/"))).toBe(false);
  });

  it("links a populated latest_run to /runs/{id} and names the dataset", async () => {
    getMock.mockResolvedValue(makeOverview());

    renderPage();

    await screen.findByText("Best accuracy");

    const runLink = screen
      .getAllByRole("link")
      .find((a) => a.getAttribute("href") === "/runs/run-9");
    expect(runLink).toBeTruthy();
    expect(runLink?.textContent).toContain("Support tickets");
  });

  it("renders the first-run empty state and no stat cards when all counts are zero", async () => {
    getMock.mockResolvedValue(
      makeOverview({
        evaluator_count: 0,
        dataset_count: 0,
        run_count: 0,
        total_rows: 0,
        labeled_rows: 0,
        best_accuracy: null,
        latest_run: null,
      }),
    );

    renderPage();

    // The empty state offers a primary action into the evaluator flow.
    const links = await screen.findAllByRole("link");
    expect(links.some((a) => a.getAttribute("href") === "/evaluators")).toBe(true);

    // The stat cards belong to the populated state and must not appear.
    expect(screen.queryByText("Best accuracy")).toBeNull();
  });

  it("renders an error banner and no stat cards when the request rejects", async () => {
    getMock.mockRejectedValue(new Error("overview unavailable"));

    renderPage();

    const banner = await screen.findByRole("alert");
    expect(banner.textContent).toContain("overview unavailable");
    expect(screen.queryByText("Best accuracy")).toBeNull();
  });
});

// The setup card sits above the stats and is driven entirely by useSetup's own fetch — it must
// neither block nor be blocked by the overview fetch. Every test here gives the overview request
// a resolved value so the stat-card assertions in the shared-rendering tests have something to
// find, and drives setup state through the same api/client mock the rest of this suite uses.
describe("OverviewPage setup card", () => {
  it("renders expanded with all three commands, marking gateway required and Logfire optional, when the gateway key is unset", async () => {
    getMock.mockResolvedValue(makeOverview());
    const status = makeSetupStatus({
      gateway_api_key: false,
      logfire_token: false,
      logfire_api_key: false,
    });
    setupGet.mockResolvedValue(status);

    renderPage();
    await screen.findByText("Overview");

    const rows = await screen.findAllByRole("listitem");
    expect(rows).toHaveLength(3);

    for (const key of status.keys) {
      const row = rows.find((candidate) => within(candidate).queryByText(key.label));
      expect(row).toBeTruthy();
      within(row as HTMLElement).getByText(key.command);
      within(row as HTMLElement).getByText(key.required ? "Required" : "Optional");
    }
  });

  it("collapses to a summary line and shows no commands when all keys are set", async () => {
    getMock.mockResolvedValue(makeOverview());
    const status = makeSetupStatus();
    setupGet.mockResolvedValue(status);

    renderPage();
    await screen.findByText("Overview");

    expect(await screen.findByText(/all setup keys are configured/i)).toBeInTheDocument();
    expect(screen.queryByRole("listitem")).toBeNull();
    for (const key of status.keys) {
      expect(screen.queryByText(key.command)).toBeNull();
    }
  });

  it("copies the right command for the right key when several are shown", async () => {
    const user = userEvent.setup();
    getMock.mockResolvedValue(makeOverview());
    const status = makeSetupStatus({
      gateway_api_key: false,
      logfire_token: false,
      logfire_api_key: true,
    });
    setupGet.mockResolvedValue(status);

    renderPage();

    // userEvent.setup() installs its own clipboard stub, so the spy must be installed after it
    // runs, matching ExportModal.test.tsx's copy-testing convention.
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
      writable: true,
    });

    const rows = await screen.findAllByRole("listitem");
    const gatewayRow = rows.find((row) => within(row).queryByText("Pydantic AI Gateway key"));
    const logfireApiKeyRow = rows.find((row) => within(row).queryByText("Logfire API key"));
    expect(gatewayRow).toBeTruthy();
    expect(logfireApiKeyRow).toBeTruthy();

    await user.click(within(gatewayRow as HTMLElement).getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenLastCalledWith(status.keys[0].command);

    await user.click(within(logfireApiKeyRow as HTMLElement).getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenLastCalledWith(status.keys[2].command);
  });

  it("Recheck triggers a second fetch and updates the card from expanded to collapsed", async () => {
    const user = userEvent.setup();
    getMock.mockResolvedValue(makeOverview());
    setupGet
      .mockResolvedValueOnce(makeSetupStatus({ gateway_api_key: false }))
      .mockResolvedValueOnce(makeSetupStatus());

    renderPage();

    await screen.findAllByRole("listitem");
    await user.click(screen.getByRole("button", { name: "Recheck" }));

    await waitFor(() => expect(screen.queryByRole("listitem")).toBeNull());
    expect(await screen.findByText(/all setup keys are configured/i)).toBeInTheDocument();
    expect(setupGet).toHaveBeenCalledTimes(2);
  });

  it("renders the existing stat cards unchanged while the setup card is expanded", async () => {
    getMock.mockResolvedValue(makeOverview());
    setupGet.mockResolvedValue(makeSetupStatus({ gateway_api_key: false }));

    renderPage();
    await screen.findAllByRole("listitem");

    expect(screen.getByText("Evaluators")).toBeTruthy();
    expect(screen.getByText("7")).toBeTruthy();
    expect(screen.getByText("Datasets")).toBeTruthy();
    expect(screen.getByText(/5 of 40 labeled/i)).toBeTruthy();
    expect(screen.getByText("Best accuracy")).toBeTruthy();
    expect(screen.getByText("91%")).toBeTruthy();
  });

  it("renders the existing stat cards unchanged while the setup card is collapsed", async () => {
    getMock.mockResolvedValue(makeOverview());
    setupGet.mockResolvedValue(makeSetupStatus());

    renderPage();
    await screen.findByText(/all setup keys are configured/i);

    expect(screen.getByText("Evaluators")).toBeTruthy();
    expect(screen.getByText("7")).toBeTruthy();
    expect(screen.getByText("Datasets")).toBeTruthy();
    expect(screen.getByText("Best accuracy")).toBeTruthy();
    expect(screen.getByText("91%")).toBeTruthy();
  });

  it("does not blank the page when the setup fetch rejects — stats still render", async () => {
    getMock.mockResolvedValue(makeOverview());
    setupGet.mockRejectedValue(new Error("setup unavailable"));

    renderPage();

    expect(await screen.findByText("Best accuracy")).toBeTruthy();
    expect(screen.getByText("91%")).toBeTruthy();
    expect(screen.getByText("Evaluators")).toBeTruthy();
  });
});
