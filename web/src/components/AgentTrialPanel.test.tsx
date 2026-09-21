// Tests for AgentTrialPanel: run an agent version against ad hoc inputs, view the
// prompt/output/latency it produced, and optionally save that response against a
// dataset. AgentTrialPanel owns its own API traffic (a later task wires it into
// AgentDetail), so this suite exercises that traffic directly -- rendering one input
// per required column, running a trial, the ephemeral-result confirm-before-rerun
// gate, saving a derivation, and discarding.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AgentTrialPanel from "./AgentTrialPanel";
import { ApiError, agents, datasets } from "../api/client";
import type { AgentVersion, DatasetSummary, Derivation, TrialResult } from "../api/types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    agents: { ...actual.agents, trial: vi.fn(), saveDerivation: vi.fn() },
    datasets: { ...actual.datasets, list: vi.fn() },
  };
});

const trialMock = vi.mocked(agents.trial);
const saveDerivationMock = vi.mocked(agents.saveDerivation);
const datasetsListMock = vi.mocked(datasets.list);

function makeVersion(overrides: Partial<AgentVersion> = {}): AgentVersion {
  return {
    id: "av-1",
    created_at: "2026-01-01T00:00:00Z",
    agent_id: "agent-1",
    version_name: "v1",
    notes: "",
    frozen: false,
    model: "gateway/openai:gpt-4o",
    spec: {},
    prompt_template: "{question}",
    required_columns: ["question"],
    deps_mapping: {},
    response_columns: ["answer"],
    ...overrides,
  };
}

function makeTrialResult(overrides: Partial<TrialResult> = {}): TrialResult {
  return {
    prompt: "What is 2+2?",
    deps: {},
    output: { answer: "4" },
    response_columns: ["answer"],
    latency_ms: 120,
    usage: null,
    error: null,
    ...overrides,
  };
}

function makeDataset(overrides: Partial<DatasetSummary> = {}): DatasetSummary {
  return {
    id: "ds-1",
    created_at: "2026-01-01T00:00:00Z",
    name: "My dataset",
    description: "",
    columns: ["question", "answer"],
    row_count: 5,
    labeled_count: 0,
    ...overrides,
  };
}

function makeDerivation(overrides: Partial<Derivation> = {}): Derivation {
  return {
    id: "der-1",
    created_at: "2026-01-01T00:00:00Z",
    dataset_id: "ds-1",
    dataset_name: "My dataset",
    agent_version_id: "av-1",
    agent_name: "My agent",
    version_name: "v1",
    ordinal: 7,
    response_columns: ["answer"],
    response_count: 1,
    ...overrides,
  };
}

beforeEach(() => {
  datasetsListMock.mockResolvedValue([makeDataset()]);
  trialMock.mockResolvedValue(makeTrialResult());
  saveDerivationMock.mockResolvedValue(makeDerivation());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function runTrial(user: ReturnType<typeof userEvent.setup>, value = "What is 2+2?") {
  await user.type(screen.getByLabelText("question"), value);
  await user.click(screen.getByRole("button", { name: "Run" }));
}

// Only the confirm action (never "Cancel") lives beside it in the ConfirmDialog footer,
// so this is the one implementation-agnostic way to click it without knowing the exact
// confirm label the component chooses.
function confirmButtonIn(dialog: HTMLElement): HTMLElement {
  const button = within(dialog)
    .getAllByRole("button")
    .find((b) => b.textContent !== "Cancel");
  if (!button) throw new Error("no confirm button found in dialog");
  return button;
}

describe("AgentTrialPanel", () => {
  it("renders one labelled input per required column", () => {
    render(<AgentTrialPanel version={makeVersion({ required_columns: ["question", "context"] })} />);

    expect(screen.getByLabelText("question")).toBeInTheDocument();
    expect(screen.getByLabelText("context")).toBeInTheDocument();
  });

  it("runs a trial with the typed inputs and renders the prompt, output, and latency", async () => {
    const user = userEvent.setup();
    render(<AgentTrialPanel version={makeVersion()} />);

    await runTrial(user);

    await waitFor(() =>
      expect(trialMock).toHaveBeenCalledWith("av-1", { inputs: { question: "What is 2+2?" } }),
    );
    expect(await screen.findByText("What is 2+2?")).toBeInTheDocument();
    expect(screen.getByLabelText("answer")).toHaveTextContent("4");
    expect(screen.getByText(/120/)).toBeInTheDocument();
  });

  it("disables Run and shows a busy state while the trial is in flight", async () => {
    let resolveTrial!: (value: TrialResult) => void;
    trialMock.mockReturnValue(
      new Promise((resolve) => {
        resolveTrial = resolve;
      }),
    );
    const user = userEvent.setup();
    render(<AgentTrialPanel version={makeVersion()} />);

    await user.type(screen.getByLabelText("question"), "hi");
    await user.click(screen.getByRole("button", { name: "Run" }));

    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
    expect(screen.getByRole("status")).toBeInTheDocument();

    resolveTrial(makeTrialResult());
    await waitFor(() => expect(screen.getByRole("button", { name: "Run" })).not.toBeDisabled());
  });

  it("shows a returned in-band error prominently instead of the output fields", async () => {
    trialMock.mockResolvedValue(
      makeTrialResult({ output: {}, error: "Tool timed out", latency_ms: 50 }),
    );
    const user = userEvent.setup();
    render(<AgentTrialPanel version={makeVersion()} />);

    await runTrial(user);

    expect(await screen.findByText("Tool timed out")).toBeInTheDocument();
    expect(screen.queryByLabelText("answer")).not.toBeInTheDocument();
  });

  it("shows the ApiError message and keeps the previous result visible without clearing it", async () => {
    const user = userEvent.setup();
    render(<AgentTrialPanel version={makeVersion()} />);

    await runTrial(user);
    expect(await screen.findByText("What is 2+2?")).toBeInTheDocument();

    // Re-running an unsaved result requires confirming first.
    trialMock.mockRejectedValueOnce(new ApiError("Trial failed", "ContractError", 422));
    await user.click(screen.getByRole("button", { name: "Run" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(confirmButtonIn(dialog));

    expect(await screen.findByText("Trial failed")).toBeInTheDocument();
    // The prior successful result must still be on screen -- a failed rerun must not
    // silently wipe out the last good trial.
    expect(screen.getByText("What is 2+2?")).toBeInTheDocument();
    expect(screen.getByLabelText("answer")).toHaveTextContent("4");
  });

  it("shows a confirmation before re-running an unsaved result, and only reruns on confirm", async () => {
    const user = userEvent.setup();
    render(<AgentTrialPanel version={makeVersion()} />);

    await runTrial(user);
    await waitFor(() => expect(trialMock).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole("button", { name: "Run" }));

    expect(
      await screen.findByText("Running again will discard the unsaved response."),
    ).toBeInTheDocument();
    expect(trialMock).toHaveBeenCalledTimes(1);

    const dialog = screen.getByRole("dialog");
    await user.click(confirmButtonIn(dialog));

    await waitFor(() => expect(trialMock).toHaveBeenCalledTimes(2));
  });

  it("does not rerun when the confirmation is cancelled", async () => {
    const user = userEvent.setup();
    render(<AgentTrialPanel version={makeVersion()} />);

    await runTrial(user);
    await waitFor(() => expect(trialMock).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole("button", { name: "Run" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(trialMock).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // The prior result is untouched by a cancelled rerun.
    expect(screen.getByText("What is 2+2?")).toBeInTheDocument();
  });

  it("keeps Save disabled until a dataset is chosen", async () => {
    const user = userEvent.setup();
    render(<AgentTrialPanel version={makeVersion()} />);

    await runTrial(user);
    await screen.findByText("What is 2+2?");

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    await user.selectOptions(await screen.findByLabelText("Dataset"), "ds-1");

    expect(screen.getByRole("button", { name: "Save" })).not.toBeDisabled();
  });

  it("keeps Save disabled when the result has an error, even with a dataset chosen", async () => {
    trialMock.mockResolvedValue(makeTrialResult({ output: {}, error: "boom" }));
    const user = userEvent.setup();
    render(<AgentTrialPanel version={makeVersion()} />);

    await runTrial(user);
    await screen.findByText("boom");
    await user.selectOptions(await screen.findByLabelText("Dataset"), "ds-1");

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("saves one entry carrying inputs and data, then no longer prompts to confirm on Run", async () => {
    const user = userEvent.setup();
    render(<AgentTrialPanel version={makeVersion()} />);

    await runTrial(user);
    await screen.findByText("What is 2+2?");
    await user.selectOptions(await screen.findByLabelText("Dataset"), "ds-1");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(saveDerivationMock).toHaveBeenCalledWith(
        "av-1",
        expect.objectContaining({
          dataset_id: "ds-1",
          entries: [
            expect.objectContaining({
              inputs: { question: "What is 2+2?" },
              data: { answer: "4" },
            }),
          ],
        }),
      ),
    );
    expect(await screen.findByText(/7/)).toBeInTheDocument();

    // unsaved is now false: Run must not ask for confirmation again.
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(trialMock).toHaveBeenCalledTimes(2));
  });

  it("discards the result, clearing it and the unsaved flag", async () => {
    const user = userEvent.setup();
    render(<AgentTrialPanel version={makeVersion()} />);

    await runTrial(user);
    await screen.findByText("What is 2+2?");

    await user.click(screen.getByRole("button", { name: "Discard" }));

    expect(screen.queryByText("What is 2+2?")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("answer")).not.toBeInTheDocument();

    // unsaved is now false: Run must not ask for confirmation.
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("registers a beforeunload warning while unsaved and removes it once saved", async () => {
    const addSpy = vi.spyOn(window, "addEventListener");
    const removeSpy = vi.spyOn(window, "removeEventListener");
    const user = userEvent.setup();
    render(<AgentTrialPanel version={makeVersion()} />);

    await runTrial(user);
    await screen.findByText("What is 2+2?");

    expect(addSpy).toHaveBeenCalledWith("beforeunload", expect.any(Function));

    await user.selectOptions(await screen.findByLabelText("Dataset"), "ds-1");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(saveDerivationMock).toHaveBeenCalled());

    expect(removeSpy).toHaveBeenCalledWith("beforeunload", expect.any(Function));
  });

  it("loads the dataset list on mount", () => {
    render(<AgentTrialPanel version={makeVersion()} />);

    expect(datasetsListMock).toHaveBeenCalled();
  });
});
