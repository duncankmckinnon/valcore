import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import OverviewPage from "./OverviewPage";
import { overview } from "../api/client";
import type { Overview } from "../api/types";

// Only the overview endpoint is exercised here; the page makes exactly one
// request on mount. Keep every other client member intact so the module loads.
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    overview: { ...actual.overview, get: vi.fn() },
  };
});

const getMock = vi.mocked(overview.get);

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

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <OverviewPage />
    </MemoryRouter>,
  );
}

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
