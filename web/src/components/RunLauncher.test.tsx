// Tests for RunLauncher: pick an evaluator, one of its versions, and a dataset, choose
// the run kind and concurrency, then Start. RunLauncher owns its own API traffic (it is
// stubbed in RunsPage.test.tsx), so this suite exercises that traffic directly — loading
// the evaluator/dataset lists, resolving versions for the selected evaluator, falling
// back off "validation" for an unlabeled dataset, submitting, and the shared gateway gate
// that also covers generation and refinement.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RunLauncher from "./RunLauncher";
import { ApiError, datasets, evaluators, runs } from "../api/client";
import { GATEWAY_BLOCKER, useSetup } from "./useSetup";
import type { UseSetupResult } from "./useSetup";
import type { Dataset, DatasetStats, Evaluator, EvaluatorVersion, Run } from "../api/types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    evaluators: { ...actual.evaluators, list: vi.fn(), get: vi.fn() },
    datasets: { ...actual.datasets, list: vi.fn(), stats: vi.fn() },
    runs: { ...actual.runs, create: vi.fn() },
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
const datasetsStatsMock = vi.mocked(datasets.stats);
const runsCreateMock = vi.mocked(runs.create);
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

function makeDataset(overrides: Partial<Dataset> = {}): Dataset {
  return {
    id: "ds-1",
    created_at: "2026-01-01T00:00:00Z",
    name: "My dataset",
    description: "",
    columns: ["answer"],
    label_schema: {},
    row_count: 10,
    labeled_count: 10,
    ...overrides,
  };
}

function makeStats(overrides: Partial<DatasetStats> = {}): DatasetStats {
  return {
    total: 10,
    labeled: 10,
    unlabeled: 0,
    label_distribution: {},
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
}

beforeEach(() => {
  useSetupMock.mockReturnValue(makeSetupResult());
  evaluatorsListMock.mockResolvedValue([makeEvaluator()]);
  datasetsListMock.mockResolvedValue([makeDataset()]);
  evaluatorsGetMock.mockResolvedValue({
    ...makeEvaluator(),
    versions: [makeVersion()],
  } as unknown as Evaluator);
  datasetsStatsMock.mockResolvedValue(makeStats());
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

  it("falls back off validation to eval when the selected dataset has unlabeled rows", async () => {
    datasetsStatsMock.mockResolvedValue(makeStats({ unlabeled: 3, labeled: 7 }));
    const user = userEvent.setup();
    renderLauncher();

    await selectFullRun(user);
    await user.selectOptions(screen.getByLabelText("Run kind"), "validation");

    await waitFor(() =>
      expect((screen.getByLabelText("Run kind") as HTMLSelectElement).value).toBe("eval"),
    );
    expect(screen.getByText(/unlabeled/i)).toBeInTheDocument();
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
