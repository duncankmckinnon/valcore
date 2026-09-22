// Derive runs stage agent responses before saving them as a numbered dataset view.
// These tests protect the review controls that make that staging boundary visible.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import RunDetail from "./RunDetail";
import { agents, datasets, runs } from "../api/client";
import type { Derivation, DerivedRowsPage, Run } from "../api/types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    agents: {
      ...actual.agents,
      acceptDerivation: vi.fn(),
      deleteDerivation: vi.fn(),
      derivedRows: vi.fn(),
      listDerivations: vi.fn(),
    },
    datasets: { ...actual.datasets, stats: vi.fn() },
    runs: { ...actual.runs, get: vi.fn(), results: vi.fn() },
  };
});

const acceptDerivationMock = vi.mocked(agents.acceptDerivation);
const deleteDerivationMock = vi.mocked(agents.deleteDerivation);
const derivedRowsMock = vi.mocked(agents.derivedRows);
const listDerivationsMock = vi.mocked(agents.listDerivations);
const statsMock = vi.mocked(datasets.stats);
const getRunMock = vi.mocked(runs.get);
const resultsMock = vi.mocked(runs.results);

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    id: "run-abcdef01",
    created_at: "2026-01-01T00:00:00Z",
    kind: "derive",
    version_id: "agent-ver-1",
    dataset_id: "ds-1",
    derivation_id: "der-1",
    status: "completed",
    concurrency: 1,
    started_at: "2026-01-01T00:00:00Z",
    finished_at: "2026-01-01T00:01:00Z",
    metrics: { scored: 2 },
    error: null,
    cancel_requested: false,
    ...overrides,
  };
}

function makeDerivation(overrides: Partial<Derivation> = {}): Derivation {
  return {
    id: "der-1",
    created_at: "2026-01-01T00:00:00Z",
    dataset_id: "ds-1",
    dataset_name: "My dataset",
    agent_version_id: "agent-ver-1",
    agent_name: "Support agent",
    version_name: "v2",
    ordinal: 0,
    response_columns: ["draft"],
    response_count: 2,
    state: "staged",
    ...overrides,
  };
}

const DERIVED_ROWS: DerivedRowsPage = {
  columns: ["prompt", "draft"],
  rows: [
    {
      row_id: "row-1",
      idx: 0,
      data: { prompt: "Help me", draft: "A helpful reply" },
      latency_ms: 120,
      error: null,
    },
  ],
};

function renderDetail(): void {
  render(
    <MemoryRouter>
      <RunDetail runId="run-abcdef01" />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  getRunMock.mockResolvedValue(makeRun());
  statsMock.mockResolvedValue({
    total: 2,
    labeled: 0,
    unlabeled: 2,
    label_distribution: {},
  });
  resultsMock.mockResolvedValue({
    results: [],
    total: 0,
    limit: 500,
    offset: 0,
  });
  listDerivationsMock.mockResolvedValue([makeDerivation()]);
  derivedRowsMock.mockResolvedValue(DERIVED_ROWS);
  acceptDerivationMock.mockResolvedValue(
    makeDerivation({ state: "saved", ordinal: 4 }),
  );
  deleteDerivationMock.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RunDetail derive runs", () => {
  it("does not request evaluator results for a derive run", async () => {
    renderDetail();

    await screen.findByText("A helpful reply");
    expect(resultsMock).not.toHaveBeenCalled();
  });

  it("shows a completed staged derivation's rows with Save and Discard actions", async () => {
    renderDetail();

    expect(await screen.findByText("A helpful reply")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Discard" })).toBeInTheDocument();
  });

  it("saves a staged derivation and replaces its actions with the allocated ordinal", async () => {
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(acceptDerivationMock).toHaveBeenCalledWith("der-1"),
    );
    expect(await screen.findByText(/derivation 4/i)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Save" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Discard" }),
    ).not.toBeInTheDocument();
  });

  it("disables Discard while Save is in progress", async () => {
    let resolveSave!: (value: Derivation) => void;
    acceptDerivationMock.mockReturnValue(
      new Promise((resolve) => {
        resolveSave = resolve;
      }),
    );
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "Save" }));

    expect(screen.getByRole("button", { name: "Discard" })).toBeDisabled();
    await act(async () => {
      resolveSave(makeDerivation({ state: "saved", ordinal: 4 }));
    });
    expect(await screen.findByText(/derivation 4/i)).toBeInTheDocument();
  });

  it("shows a saved derivation's ordinal without staging actions", async () => {
    listDerivationsMock.mockResolvedValue([
      makeDerivation({ state: "saved", ordinal: 7 }),
    ]);
    renderDetail();

    expect(await screen.findByText(/derivation 7/i)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Save" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Discard" }),
    ).not.toBeInTheDocument();
  });

  it("requires confirmation before discarding a staged derivation", async () => {
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "Discard" }));

    expect(deleteDerivationMock).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Discard" }));
    await waitFor(() =>
      expect(deleteDerivationMock).toHaveBeenCalledWith("der-1"),
    );
    expect(
      screen.queryByRole("button", { name: "Save" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Discard" }),
    ).not.toBeInTheDocument();
  });

  it("disables Save while Discard is in progress", async () => {
    let resolveDiscard!: () => void;
    deleteDerivationMock.mockReturnValue(
      new Promise((resolve) => {
        resolveDiscard = resolve;
      }),
    );
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "Discard" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Discard" }));

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await act(async () => {
      resolveDiscard();
    });
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Save" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("keeps dataset values visible and presents response errors separately", async () => {
    derivedRowsMock.mockResolvedValue({
      columns: ["prompt", "draft"],
      rows: [
        {
          row_id: "row-error",
          idx: 0,
          data: { prompt: "Keep this input" },
          latency_ms: 25,
          error: "Agent timed out",
        },
      ],
    });
    renderDetail();

    const inputCell = await screen.findByRole("cell", {
      name: "Keep this input",
    });
    expect(inputCell).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "Error" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("cell", { name: "Agent timed out" }),
    ).toBeInTheDocument();
  });

  it("displays skipped-row reasons when metrics report an incomplete scoring pass", async () => {
    getRunMock.mockResolvedValue(
      makeRun({
        kind: "eval",
        metrics: {
          accuracy: 0.75,
          scored: 3,
          skipped: { no_response: 1, error: 2 },
        },
      }),
    );
    renderDetail();

    expect(await screen.findByText(/no response.*1/i)).toBeInTheDocument();
    expect(screen.getByText(/error.*2/i)).toBeInTheDocument();
  });
});
