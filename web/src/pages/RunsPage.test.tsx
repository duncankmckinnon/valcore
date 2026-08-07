import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import RunsPage from "./RunsPage";
import { datasets, evaluators, runs } from "../api/client";
import type { Dataset, Evaluator, EvaluatorVersion, Run } from "../api/types";

// RunLauncher owns its own API traffic and is exercised by its own suite; stub it
// so opening the "New run" modal is observable without standing up its fixtures.
vi.mock("../components/RunLauncher", () => ({
  default: () => <p>run launcher</p>,
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    runs: { ...actual.runs, list: vi.fn() },
    datasets: { ...actual.datasets, list: vi.fn() },
    evaluators: { ...actual.evaluators, list: vi.fn(), get: vi.fn() },
  };
});

const runsListMock = vi.mocked(runs.list);
const datasetsListMock = vi.mocked(datasets.list);
const evaluatorsListMock = vi.mocked(evaluators.list);
const evaluatorsGetMock = vi.mocked(evaluators.get);

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    id: "run-abcdef01",
    created_at: "2026-01-01T00:00:00Z",
    kind: "eval",
    version_id: "ver-1",
    dataset_id: "ds-1",
    status: "completed",
    concurrency: 1,
    started_at: "2026-01-01T00:00:00Z",
    finished_at: "2026-01-01T00:01:00Z",
    metrics: { accuracy: 0.9 },
    error: null,
    cancel_requested: false,
    ...overrides,
  };
}

const DATASET: Dataset = {
  id: "ds-1",
  created_at: "2026-01-01T00:00:00Z",
  name: "My dataset",
  description: "",
  columns: [],
  label_schema: {},
  row_count: 0,
  labeled_count: 0,
};

const EVALUATOR: Evaluator = {
  id: "ev-1",
  created_at: "2026-01-01T00:00:00Z",
  name: "My evaluator",
  description: "",
  active_version_id: "ver-1",
};

const VERSION = {
  id: "ver-1",
  version_name: "v1",
} as EvaluatorVersion;

// A sentinel route so navigation off the index (to a run or to compare) is
// observable as text without mounting the real detail/compare views.
function LocationProbe() {
  const location = useLocation();
  return <p>at {location.pathname}</p>;
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/runs"]}>
      <Routes>
        <Route path="/runs" element={<RunsPage />} />
        <Route path="/runs/compare" element={<LocationProbe />} />
        <Route path="/runs/:id" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  runsListMock.mockResolvedValue([]);
  datasetsListMock.mockResolvedValue([]);
  evaluatorsListMock.mockResolvedValue([]);
  evaluatorsGetMock.mockResolvedValue(EVALUATOR);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RunsPage chrome", () => {
  it("renders exactly one level-1 heading titled Runs", async () => {
    renderPage();

    await waitFor(() => expect(runsListMock).toHaveBeenCalled());

    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent("Runs");
  });

  it("describes what a run does in the header", async () => {
    renderPage();

    await waitFor(() => expect(runsListMock).toHaveBeenCalled());

    // The PageHeader description explains that a run applies an evaluator version
    // to a dataset and records how well it scored.
    expect(screen.getByText(/how well it scored/i)).toBeTruthy();
  });
});

describe("RunsPage empty state", () => {
  it("explains what a run is when there are no runs", async () => {
    renderPage();

    // The bare "No runs yet." string is replaced by an EmptyState whose message
    // explains that a run scores an evaluator version against a dataset.
    expect(await screen.findByText(/evaluator version against a dataset/i)).toBeTruthy();
  });

  it("offers a launch action from the empty state", async () => {
    renderPage();

    await waitFor(() => expect(runsListMock).toHaveBeenCalled());

    // Both the header and the empty state surface the launch action when empty.
    expect(screen.getAllByRole("button", { name: /new run/i }).length).toBeGreaterThan(0);
  });
});

describe("RunsPage list and navigation", () => {
  it("lists existing runs with a link to each run detail", async () => {
    runsListMock.mockResolvedValue([makeRun()]);
    datasetsListMock.mockResolvedValue([DATASET]);
    evaluatorsListMock.mockResolvedValue([EVALUATOR]);
    evaluatorsGetMock.mockResolvedValue({
      ...EVALUATOR,
      versions: [VERSION],
    } as unknown as Evaluator);

    renderPage();

    const link = await screen.findByRole("link", { name: /My evaluator \/ v1/ });
    expect(link.getAttribute("href")).toBe("/runs/run-abcdef01");
  });

  it("navigates to the run detail when a run link is clicked", async () => {
    runsListMock.mockResolvedValue([makeRun()]);
    datasetsListMock.mockResolvedValue([DATASET]);
    evaluatorsListMock.mockResolvedValue([EVALUATOR]);
    evaluatorsGetMock.mockResolvedValue({
      ...EVALUATOR,
      versions: [VERSION],
    } as unknown as Evaluator);

    renderPage();

    await userEvent.click(await screen.findByRole("link", { name: /My evaluator \/ v1/ }));

    expect(await screen.findByText("at /runs/run-abcdef01")).toBeTruthy();
  });

  it("navigates to compare when the Compare action is clicked", async () => {
    runsListMock.mockResolvedValue([makeRun()]);

    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Compare" }));

    expect(await screen.findByText("at /runs/compare")).toBeTruthy();
  });

  it("opens the New run modal when a launch action is clicked", async () => {
    // A non-empty list keeps the single header launch action unambiguous.
    runsListMock.mockResolvedValue([makeRun()]);

    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: /new run/i }));

    expect(await screen.findByText("run launcher")).toBeTruthy();
  });
});
