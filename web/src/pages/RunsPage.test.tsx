import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import RunsPage from "./RunsPage";
import { agents, datasets, evaluators, runs } from "../api/client";
import type {
  AgentSummary,
  AgentVersion,
  DatasetSummary,
  Derivation,
  Evaluator,
  EvaluatorVersion,
  Run,
} from "../api/types";

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
    agents: {
      ...actual.agents,
      list: vi.fn(),
      listVersions: vi.fn(),
      listDerivations: vi.fn(),
    },
  };
});

const runsListMock = vi.mocked(runs.list);
const datasetsListMock = vi.mocked(datasets.list);
const evaluatorsListMock = vi.mocked(evaluators.list);
const evaluatorsGetMock = vi.mocked(evaluators.get);
const agentsListMock = vi.mocked(agents.list);
const agentVersionsMock = vi.mocked(agents.listVersions);
const listDerivationsMock = vi.mocked(agents.listDerivations);

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    id: "run-abcdef01",
    created_at: "2026-01-01T00:00:00Z",
    kind: "eval",
    version_id: "ver-1",
    dataset_id: "ds-1",
    derivation_id: null,
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

const DATASET: DatasetSummary = {
  id: "ds-1",
  created_at: "2026-01-01T00:00:00Z",
  name: "My dataset",
  description: "",
  columns: [],
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

const AGENT: AgentSummary = {
  id: "agent-1",
  created_at: "2026-01-01T00:00:00Z",
  name: "Support agent",
  description: "",
  active_version_id: "agent-ver-1",
  version_count: 1,
};

const AGENT_VERSION = {
  id: "agent-ver-1",
  agent_id: "agent-1",
  version_name: "v2",
} as AgentVersion;

function makeDerivation(overrides: Partial<Derivation> = {}): Derivation {
  return {
    id: "der-1",
    created_at: "2026-01-01T00:00:00Z",
    dataset_id: "ds-1",
    dataset_name: "My dataset",
    agent_version_id: "agent-ver-1",
    agent_name: "Support agent",
    version_name: "v2",
    ordinal: 3,
    response_columns: ["draft"],
    response_count: 5,
    state: "saved",
    ...overrides,
  };
}

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

function renderComparePage() {
  render(
    <MemoryRouter initialEntries={["/runs/compare"]}>
      <Routes>
        <Route path="/runs/compare" element={<RunsPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  runsListMock.mockResolvedValue([]);
  datasetsListMock.mockResolvedValue([]);
  evaluatorsListMock.mockResolvedValue([]);
  evaluatorsGetMock.mockResolvedValue(EVALUATOR);
  agentsListMock.mockResolvedValue([]);
  agentVersionsMock.mockResolvedValue([]);
  listDerivationsMock.mockResolvedValue([]);
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

  it("describes both kinds of run in the header", async () => {
    renderPage();

    await waitFor(() => expect(runsListMock).toHaveBeenCalled());

    expect(screen.getByText(/derive agent responses/i)).toBeTruthy();
  });
});

describe("RunsPage empty state", () => {
  it("explains what a run is when there are no runs", async () => {
    renderPage();

    expect(
      await screen.findByText(/run an agent over a dataset/i),
    ).toBeTruthy();
  });

  it("offers a launch action from the empty state", async () => {
    renderPage();

    await waitFor(() => expect(runsListMock).toHaveBeenCalled());

    // Both the header and the empty state surface the launch action when empty.
    expect(
      screen.getAllByRole("button", { name: /new run/i }).length,
    ).toBeGreaterThan(0);
  });
});

describe("RunsPage list and navigation", () => {
  it("labels a derive run with its agent version instead of resolving it as an evaluator", async () => {
    runsListMock.mockResolvedValue([
      makeRun({
        kind: "derive",
        version_id: "agent-ver-1",
        derivation_id: "der-1",
        metrics: null,
      }),
    ]);
    listDerivationsMock.mockResolvedValue([makeDerivation()]);
    agentsListMock.mockResolvedValue([AGENT]);
    agentVersionsMock.mockResolvedValue([AGENT_VERSION]);

    renderPage();

    const agentVersion = await screen.findByRole("link", {
      name: "Support agent / v2",
    });
    expect(agentVersion).toBeInTheDocument();
    expect(
      within(agentVersion.closest("tr")!).getAllByRole("cell")[4],
    ).toHaveTextContent("5 rows");
    expect(evaluatorsGetMock).not.toHaveBeenCalledWith("agent-ver-1");
  });

  it("excludes derive runs from evaluator comparison choices", async () => {
    runsListMock.mockResolvedValue([
      makeRun({ id: "eval-run-1" }),
      makeRun({ id: "derive-run-1", kind: "derive" }),
    ]);

    renderComparePage();

    const select = await screen.findByRole("combobox", { name: "Run A" });
    expect(within(select).getAllByRole("option")).toHaveLength(2);
    expect(within(select).getByRole("option", { name: /eval$/ })).toBeTruthy();
    expect(
      within(select).queryByRole("option", { name: /derive$/ }),
    ).toBeNull();
  });

  it.each([
    ["pending", null],
    ["completed", "discarded-derivation"],
  ] as const)(
    "labels a %s derive run from the agent catalog when its derivation is unavailable",
    async (status, derivationId) => {
      runsListMock.mockResolvedValue([
        makeRun({
          kind: "derive",
          version_id: "agent-ver-1",
          derivation_id: derivationId,
          status,
        }),
      ]);
      agentsListMock.mockResolvedValue([AGENT]);
      agentVersionsMock.mockResolvedValue([AGENT_VERSION]);

      renderPage();

      expect(
        await screen.findByRole("link", { name: "Support agent / v2" }),
      ).toBeInTheDocument();
    },
  );

  it("shows the derivation contract used by an evaluator run", async () => {
    runsListMock.mockResolvedValue([makeRun({ derivation_id: "der-1" })]);
    listDerivationsMock.mockResolvedValue([makeDerivation()]);

    renderPage();

    expect(
      await screen.findByText(/Support agent.*v2.*derivation 3/i),
    ).toBeInTheDocument();
  });

  it("lists existing runs with a link to each run detail", async () => {
    runsListMock.mockResolvedValue([makeRun()]);
    datasetsListMock.mockResolvedValue([DATASET]);
    evaluatorsListMock.mockResolvedValue([EVALUATOR]);
    evaluatorsGetMock.mockResolvedValue({
      ...EVALUATOR,
      versions: [VERSION],
    } as unknown as Evaluator);

    renderPage();

    const link = await screen.findByRole("link", {
      name: /My evaluator \/ v1/,
    });
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

    await userEvent.click(
      await screen.findByRole("link", { name: /My evaluator \/ v1/ }),
    );

    expect(await screen.findByText("at /runs/run-abcdef01")).toBeTruthy();
  });

  it("navigates to compare when the Compare action is clicked", async () => {
    runsListMock.mockResolvedValue([makeRun()]);

    renderPage();

    await userEvent.click(
      await screen.findByRole("button", { name: "Compare" }),
    );

    expect(await screen.findByText("at /runs/compare")).toBeTruthy();
  });

  it("opens the New run modal when a launch action is clicked", async () => {
    // A non-empty list keeps the single header launch action unambiguous.
    runsListMock.mockResolvedValue([makeRun()]);

    renderPage();

    await userEvent.click(
      await screen.findByRole("button", { name: /new run/i }),
    );

    expect(await screen.findByText("run launcher")).toBeTruthy();
  });
});
