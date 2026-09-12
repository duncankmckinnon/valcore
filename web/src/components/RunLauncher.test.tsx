// Tests for RunLauncher: pick an evaluator, one of its versions, and a dataset, choose
// the run kind and concurrency, then Start. RunLauncher owns its own API traffic (it is
// stubbed in RunsPage.test.tsx), so this suite exercises that traffic directly — loading
// the evaluator/dataset lists, resolving versions for the selected evaluator, the
// coverage-based validation gate, submitting, and the shared gateway gate that also
// covers generation and refinement.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RunLauncher from "./RunLauncher";
import { ApiError, datasets, evaluators, runs } from "../api/client";
import { GATEWAY_BLOCKER, useSetup } from "./useSetup";
import type { UseSetupResult } from "./useSetup";
import type { DatasetSummary, Evaluator, EvaluatorVersion, Run, RunCoverage } from "../api/types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    evaluators: { ...actual.evaluators, list: vi.fn(), get: vi.fn() },
    datasets: { ...actual.datasets, list: vi.fn() },
    runs: { ...actual.runs, create: vi.fn(), coverage: vi.fn() },
  };
});

// Launching a run needs the gateway key exactly as generation and refinement do; the
// hook is mocked directly so each test controls gatewayReady without re-exercising
// useSetup's own fetch machinery, which has its own dedicated suite.
vi.mock("./useSetup", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./useSetup")>();
  return { ...actual, useSetup: vi.fn() };
});

const evaluatorsListMock = vi.mocked(evaluators.list);
const evaluatorsGetMock = vi.mocked(evaluators.get);
const datasetsListMock = vi.mocked(datasets.list);
const runsCreateMock = vi.mocked(runs.create);
const runsCoverageMock = vi.mocked(runs.coverage);
const useSetupMock = vi.mocked(useSetup);

function makeSetupResult(overrides: Partial<UseSetupResult> = {}): UseSetupResult {
  return {
    status: null,
    gatewayReady: true,
    loading: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  };
}

function makeEvaluator(overrides: Partial<Evaluator> = {}): Evaluator {
  return {
    id: "ev-1",
    created_at: "2026-01-01T00:00:00Z",
    name: "My evaluator",
    description: "",
    active_version_id: "ver-1",
    ...overrides,
  };
}

function makeVersion(overrides: Partial<EvaluatorVersion> = {}): EvaluatorVersion {
  return {
    id: "ver-1",
    created_at: "2026-01-01T00:00:00Z",
    evaluator_id: "ev-1",
    version_name: "v1",
    notes: "",
    frozen: false,
    model: "model-a",
    instructions: "Judge it.",
    prompt_template: "{answer}",
    required_columns: ["answer"],
    output_fields: [],
    score_field: "verdict",
    score_kind: "categorical",
    score_labels: ["pass", "fail"],
    score_minimum: null,
    score_maximum: null,
    capabilities: [],
    tools: [],
    ...overrides,
  };
}

function makeDataset(overrides: Partial<DatasetSummary> = {}): DatasetSummary {
  return {
    id: "ds-1",
    created_at: "2026-01-01T00:00:00Z",
    name: "My dataset",
    description: "",
    columns: ["answer"],
    row_count: 10,
    labeled_count: 10,
    ...overrides,
  };
}

function makeCoverage(overrides: Partial<RunCoverage> = {}): RunCoverage {
  return {
    label_set_id: "ls-1",
    total_rows: 10,
    labeled_rows: 10,
    ...overrides,
  };
}

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    id: "run-1",
    created_at: "2026-01-01T00:00:00Z",
    kind: "eval",
    version_id: "ver-1",
    dataset_id: "ds-1",
    status: "pending",
    concurrency: 8,
    started_at: null,
    finished_at: null,
    metrics: null,
    error: null,
    cancel_requested: false,
    ...overrides,
  };
}

function renderLauncher(onStarted: (run: Run) => void = vi.fn()) {
  render(<RunLauncher onStarted={onStarted} />);
  return { onStarted };
}

async function selectFullRun(user: ReturnType<typeof userEvent.setup>) {
  await user.selectOptions(await screen.findByLabelText("Evaluator"), "ev-1");
  await waitFor(() => expect(screen.getByLabelText("Version")).not.toBeDisabled());
  await user.selectOptions(screen.getByLabelText("Dataset"), "ds-1");
  await waitFor(() => expect(runsCoverageMock).toHaveBeenCalledWith("ds-1", "ver-1"));
}

beforeEach(() => {
  useSetupMock.mockReturnValue(makeSetupResult());
  evaluatorsListMock.mockResolvedValue([makeEvaluator()]);
  datasetsListMock.mockResolvedValue([makeDataset()]);
  evaluatorsGetMock.mockResolvedValue({
    ...makeEvaluator(),
    versions: [makeVersion()],
  } as unknown as Evaluator);
  runsCoverageMock.mockResolvedValue(makeCoverage());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RunLauncher", () => {
  it("renders the evaluator, version, dataset, run kind, and concurrency fields", async () => {
    renderLauncher();

    expect(await screen.findByLabelText("Evaluator")).toBeInTheDocument();
    expect(screen.getByLabelText("Version")).toBeInTheDocument();
    expect(screen.getByLabelText("Dataset")).toBeInTheDocument();
    expect(screen.getByLabelText("Run kind")).toBeInTheDocument();
    expect(screen.getByLabelText("Concurrency")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start" })).toBeInTheDocument();
  });

  it("loads the evaluator and dataset lists on mount", async () => {
    renderLauncher();

    await waitFor(() => expect(evaluatorsListMock).toHaveBeenCalled());
    expect(datasetsListMock).toHaveBeenCalled();
  });

  it("disables Start until a version and a dataset are selected", async () => {
    renderLauncher();

    expect(await screen.findByRole("button", { name: "Start" })).toBeDisabled();

    const user = userEvent.setup();
    await selectFullRun(user);

    expect(screen.getByRole("button", { name: "Start" })).not.toBeDisabled();
  });

  it("selecting an evaluator loads its versions and preselects the active version", async () => {
    evaluatorsGetMock.mockResolvedValue({
      ...makeEvaluator({ active_version_id: "ver-1" }),
      versions: [makeVersion({ id: "ver-1", version_name: "v1" }), makeVersion({ id: "ver-2", version_name: "v2" })],
    } as unknown as Evaluator);
    const user = userEvent.setup();
    renderLauncher();

    await user.selectOptions(await screen.findByLabelText("Evaluator"), "ev-1");

    await waitFor(() => expect(screen.getByLabelText("Version")).not.toBeDisabled());
    expect((screen.getByLabelText("Version") as HTMLSelectElement).value).toBe("ver-1");
  });

  it("falls back off validation to eval when no label set matches this version", async () => {
    runsCoverageMock.mockResolvedValue(makeCoverage({ label_set_id: null, labeled_rows: 0 }));
    const user = userEvent.setup();
    renderLauncher();

    await selectFullRun(user);
    await user.selectOptions(screen.getByLabelText("Run kind"), "validation");

    await waitFor(() =>
      expect((screen.getByLabelText("Run kind") as HTMLSelectElement).value).toBe("eval"),
    );
    expect(screen.getByText(/no label set on this dataset matches/i)).toBeInTheDocument();
  });

  it("keeps validation selectable and warns on partial coverage", async () => {
    runsCoverageMock.mockResolvedValue(makeCoverage({ total_rows: 10, labeled_rows: 6 }));
    const user = userEvent.setup();
    renderLauncher();

    await selectFullRun(user);
    await user.selectOptions(screen.getByLabelText("Run kind"), "validation");

    expect((screen.getByLabelText("Run kind") as HTMLSelectElement).value).toBe("validation");
    expect(await screen.findByText(/6 of 10 rows/i)).toBeInTheDocument();
  });

  it("shows no coverage warning when every row is labeled", async () => {
    runsCoverageMock.mockResolvedValue(makeCoverage({ total_rows: 10, labeled_rows: 10 }));
    const user = userEvent.setup();
    renderLauncher();

    await selectFullRun(user);
    await user.selectOptions(screen.getByLabelText("Run kind"), "validation");

    expect(screen.queryByText(/rows have a label/i)).toBeNull();
    expect(screen.queryByText(/no label set on this dataset matches/i)).toBeNull();
  });

  it("creates the run with the selected fields and hands it to onStarted", async () => {
    runsCreateMock.mockResolvedValue(makeRun());
    const user = userEvent.setup();
    const { onStarted } = renderLauncher();

    await selectFullRun(user);
    const concurrency = screen.getByLabelText("Concurrency");
    await user.clear(concurrency);
    await user.type(concurrency, "4");
    await user.click(screen.getByRole("button", { name: "Start" }));

    await waitFor(() =>
      expect(runsCreateMock).toHaveBeenCalledWith(
        expect.objectContaining({
          kind: "eval",
          version_id: "ver-1",
          dataset_id: "ds-1",
          concurrency: 4,
        }),
      ),
    );
    expect(onStarted).toHaveBeenCalledWith(makeRun());
  });

  it("surfaces a server error and re-enables Start without calling onStarted", async () => {
    runsCreateMock.mockRejectedValue(new ApiError("Run failed", "ContractError", 422));
    const user = userEvent.setup();
    const { onStarted } = renderLauncher();

    await selectFullRun(user);
    await user.click(screen.getByRole("button", { name: "Start" }));

    expect(await screen.findByText(/Run failed/i)).toBeInTheDocument();
    expect(onStarted).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Start" })).not.toBeDisabled();
  });
});

// -- Gateway gating -----------------------------------------------------------
// A run needs the Pydantic AI Gateway key as much as generation does: launching one
// dispatches the judge agent through the gateway for every row.

describe("RunLauncher gateway gating", () => {
  it("disables Start and shows the shared gateway blocker when the gateway key is unset", async () => {
    useSetupMock.mockReturnValue(makeSetupResult({ gatewayReady: false }));
    const user = userEvent.setup();
    renderLauncher();

    await selectFullRun(user);

    expect(screen.getByText(GATEWAY_BLOCKER)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start" })).toBeDisabled();
    expect(runsCreateMock).not.toHaveBeenCalled();
  });

  it("shows no gateway blocker and governs Start only by its own validity when the gateway is ready", async () => {
    useSetupMock.mockReturnValue(makeSetupResult({ gatewayReady: true }));
    runsCreateMock.mockResolvedValue(makeRun());
    const user = userEvent.setup();
    const { onStarted } = renderLauncher();

    expect(screen.queryByText(GATEWAY_BLOCKER)).toBeNull();
    expect(await screen.findByRole("button", { name: "Start" })).toBeDisabled();

    await selectFullRun(user);
    expect(screen.getByRole("button", { name: "Start" })).not.toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
  });

  it("re-enables Start once gatewayReady flips true with valid selections already made", async () => {
    useSetupMock.mockReturnValue(makeSetupResult({ gatewayReady: false }));
    const user = userEvent.setup();
    const { rerender } = render(<RunLauncher onStarted={vi.fn()} />);

    await selectFullRun(user);
    expect(screen.getByRole("button", { name: "Start" })).toBeDisabled();

    useSetupMock.mockReturnValue(makeSetupResult({ gatewayReady: true }));
    rerender(<RunLauncher onStarted={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Start" })).not.toBeDisabled();
    expect(screen.queryByText(GATEWAY_BLOCKER)).toBeNull();
  });
});
