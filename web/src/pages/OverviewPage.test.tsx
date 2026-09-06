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
    logfire_read_key: true,
    logfire_write_key: true,
  };
  const set = { ...defaults, ...overrides };
  const fixed: Record<SetupKey["name"], Omit<SetupKey, "set">> = {
    gateway_api_key: {
      name: "gateway_api_key",
      required: true,
      label: "Pydantic AI Gateway key",
      command: "valcore config set-key",
      purpose: "Required to generate datasets and run evaluators.",
      explanation: "Gateway explanation.",
      from_env: false,
    },
    logfire_token: {
      name: "logfire_token",
      required: false,
      label: "Logfire tracing token",
      command: "valcore config set-logfire-token",
      purpose: "Sends run and row spans to Logfire for tracing.",
      explanation: "Tracing explanation.",
      from_env: false,
    },
    logfire_read_key: {
      name: "logfire_read_key",
      required: false,
      label: "Logfire read key",
      command: "valcore config set-logfire-read-key",
      purpose: "Queries traces in the Logfire project you are sampling from.",
      explanation: "Read explanation.",
      from_env: false,
    },
    logfire_write_key: {
      name: "logfire_write_key",
      required: false,
      label: "Logfire write key",
      command: "valcore config set-logfire-write-key",
      purpose: "Pushes datasets to your valcore Logfire project.",
      explanation: "Write explanation.",
      from_env: false,
    },
  };
  const names: SetupKey["name"][] = [
    "gateway_api_key",
    "logfire_token",
    "logfire_read_key",
    "logfire_write_key",
  ];
  return {
    keys: names.map((name) => ({ ...fixed[name], set: set[name] })),
    default_model: "gateway/anthropic:claude-sonnet-5",
    local_cli_default: null,
    local_cli_options: ["claude", "codex", "cursor"],
    logfire_explore_url: null,
    logfire_traces_url: null,
    logfire_datasets_url: null,
  };
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
  it("renders expanded with all four keys, marking gateway required and Logfire optional, when the gateway key is unset", async () => {
    getMock.mockResolvedValue(makeOverview());
    const status = makeSetupStatus({
      gateway_api_key: false,
      logfire_token: false,
      logfire_read_key: false,
      logfire_write_key: false,
    });
    setupGet.mockResolvedValue(status);

    renderPage();
    await screen.findByText("Overview");

    const rows = await screen.findAllByRole("listitem");
    expect(rows).toHaveLength(4);

    for (const key of status.keys) {
      const row = rows.find((candidate) => within(candidate).queryByText(key.label));
      expect(row).toBeTruthy();
      within(row as HTMLElement).getByText(key.required ? "Required" : "Optional");
      expect(within(row as HTMLElement).queryByText(key.command)).toBeNull();
    }
    expect(screen.getByRole("link", { name: "Manage keys" }).getAttribute("href")).toBe(
      "/settings",
    );
  });

  it("collapses to a summary line and still links to Settings when all keys are set", async () => {
    getMock.mockResolvedValue(makeOverview());
    const status = makeSetupStatus();
    setupGet.mockResolvedValue(status);

    renderPage();
    await screen.findByText("Overview");

    expect(await screen.findByText(/all setup keys are configured/i)).toBeInTheDocument();
    expect(screen.queryByRole("listitem")).toBeNull();
    expect(screen.getByRole("link", { name: "Manage keys" }).getAttribute("href")).toBe(
      "/settings",
    );
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

describe("OverviewPage default model card", () => {
  it("shows the local CLI as the default model when one is selected", async () => {
    getMock.mockResolvedValue(makeOverview());
    setupGet.mockResolvedValue({
      ...makeSetupStatus(),
      local_cli_default: "claude",
      default_model: "local/claude",
    });
    render(<OverviewPage />, { wrapper: MemoryRouter });

    await screen.findByText(/local cli: claude/i);
  });

  it("shows the resolved default_model even when local_cli_default is stale relative to an env override", async () => {
    // VALCORE_DEFAULT_MODEL outranks config.toml's local_cli_default, so the backend can report a
    // stored local CLI alongside a resolved gateway model. The card must follow default_model.
    getMock.mockResolvedValue(makeOverview());
    setupGet.mockResolvedValue({
      ...makeSetupStatus(),
      local_cli_default: "codex",
      default_model: "gateway/openai:gpt-5",
    });
    render(<OverviewPage />, { wrapper: MemoryRouter });

    await screen.findByText("gateway/openai:gpt-5");
    expect(screen.queryByText(/local cli: codex/i)).not.toBeInTheDocument();
  });

  it("shows a not-set call to action when neither a gateway key nor a local CLI is configured", async () => {
    getMock.mockResolvedValue(makeOverview());
    setupGet.mockResolvedValue(makeSetupStatus({ gateway_api_key: false }));
    render(<OverviewPage />, { wrapper: MemoryRouter });

    // Scoped to the default-model card itself: with the gateway key unset, SetupCard's own
    // expanded key row also renders a "Not set" span, so an unscoped findByText(/not set/i)
    // matches two elements.
    const label = await screen.findByText("Default model");
    const card = label.closest(".default-model-card") as HTMLElement;
    expect(within(card).getByText(/not set/i)).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: /settings/i })).toBeInTheDocument();
  });
});
