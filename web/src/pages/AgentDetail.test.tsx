// The agent detail page binds stored agent versions to the raw-spec editor and trial
// panel. These tests keep the page wiring separate from AgentTrialPanel's own behavior.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AgentDetail from "./AgentDetail";
import { ApiError, agents, api, datasets, evaluators, runs, setup } from "../api/client";
import type {
  AgentDetail as AgentDetailData,
  AgentVersion,
  DatasetSummary,
  GeneratedConfig,
  SetupStatus,
  Run,
} from "../api/types";

const navigate = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("../components/AgentTrialPanel", () => ({
  default: ({ version, onUnsavedChange }: { version: AgentVersion; onUnsavedChange?: (unsaved: boolean) => void }) => (
    <div data-testid="trial-panel">
      Trial panel for {version.id}
      <button onClick={() => onUnsavedChange?.(true)}>Simulate response</button>
    </div>
  ),
}));

vi.mock("../components/AgentPromptSync", () => ({
  default: ({
    agentId,
    open,
    onClose,
    onPulled,
  }: {
    agentId: string;
    open: boolean;
    onClose: () => void;
    onPulled: (activeVersionId: string | null) => void;
  }) =>
    open ? (
      <div role="dialog" aria-label="Logfire sync">
        Prompt sync for {agentId}
        <button onClick={() => onPulled("av-2")}>Simulate pull</button>
        <button onClick={onClose}>Close sync</button>
      </div>
    ) : null,
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    agents: {
      ...actual.agents,
      get: vi.fn(),
      createVersion: vi.fn(),
      updateVersion: vi.fn(),
      freezeVersion: vi.fn(),
      copyVersion: vi.fn(),
      removeVersion: vi.fn(),
      exportVersion: vi.fn(),
      importSpec: vi.fn(),
      promptSyncStatus: vi.fn(),
      promptSyncLink: vi.fn(),
      promptSyncPull: vi.fn(),
      promptSyncPush: vi.fn(),
      promptSyncResolve: vi.fn(),
      promptSyncUnlink: vi.fn(),
    },
    datasets: { ...actual.datasets, list: vi.fn() },
    evaluators: { ...actual.evaluators, generate: vi.fn() },
    runs: { ...actual.runs, create: vi.fn() },
    setup: { ...actual.setup, get: vi.fn() },
    api: vi.fn(),
  };
});

function makeDataset(overrides: Partial<DatasetSummary> = {}): DatasetSummary {
  return {
    id: "dataset-1",
    created_at: "2026-09-20T00:00:00Z",
    name: "Support conversations",
    description: "Customer questions for the support agent.",
    columns: ["question", "context"],
    row_count: 10,
    labeled_count: 0,
    ...overrides,
  };
}

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    id: "run-derive-1",
    created_at: "2026-09-20T00:00:00Z",
    kind: "derive",
    version_id: "av-1",
    dataset_id: "dataset-1",
    derivation_id: null,
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

function makeVersion(overrides: Partial<AgentVersion> = {}): AgentVersion {
  return {
    id: "av-1",
    created_at: "2026-09-20T00:00:00Z",
    agent_id: "agent-1",
    version_name: "initial",
    notes: "First revision.",
    frozen: false,
    model: "gateway/openai:gpt-4o",
    spec: { name: "support-agent", instructions: ["Help the user."] },
    prompt_template: "Question: {question}",
    required_columns: ["question", "context"],
    deps_mapping: { account_id: "account" },
    response_columns: ["answer", "confidence"],
    ...overrides,
  };
}

function makeDetail(overrides: Partial<AgentDetailData> = {}): AgentDetailData {
  return {
    agent: {
      id: "agent-1",
      created_at: "2026-09-20T00:00:00Z",
      name: "Support agent",
      description: "Answers customer questions.",
      active_version_id: "av-1",
      version_count: 1,
    },
    versions: [makeVersion()],
    ...overrides,
  };
}

function renderDetail(agentId = "agent-1") {
  return render(<AgentDetail agentId={agentId} />);
}

beforeEach(() => {
  vi.mocked(api).mockResolvedValue({
    models: ["gateway/openai:gpt-4o"],
    default_model: "gateway/openai:gpt-4o",
    tools: [],
    capabilities: [],
  });
  vi.mocked(datasets.list).mockResolvedValue([makeDataset()]);
  vi.mocked(setup.get).mockResolvedValue({
    keys: [{ name: "gateway_api_key", set: true }],
    local_cli_default: null,
    local_cli_options: ["claude", "codex", "cursor"],
  } as SetupStatus);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AgentDetail", () => {
  it("starts a derive run for the selected version and opens its run detail", async () => {
    vi.mocked(agents.get).mockResolvedValue(makeDetail());
    vi.mocked(runs.create).mockResolvedValue(makeRun());
    const user = userEvent.setup();
    renderDetail();

    await screen.findByRole("heading", { name: "Support agent" });
    await user.click(
      screen.getByRole("button", { name: "Run agent" }),
    );
    expect(screen.getByRole("dialog", { name: "Run agent" }).classList.contains("modal-side")).toBe(true);
    expect(screen.getByTestId("trial-panel")).toHaveTextContent("av-1");
    await user.click(screen.getByRole("tab", { name: "Run a dataset" }));
    expect(screen.getByTestId("trial-panel")).toHaveTextContent("av-1");
    await user.selectOptions(
      await screen.findByLabelText("Dataset"),
      "dataset-1",
    );
    await user.click(screen.getByRole("button", { name: "Run dataset" }));

    await waitFor(() =>
      expect(runs.create).toHaveBeenCalledWith(
        expect.objectContaining({
          kind: "derive",
          version_id: "av-1",
          dataset_id: "dataset-1",
        }),
      ),
    );
    const payload = vi.mocked(runs.create).mock.calls[0]?.[0];
    expect(payload).not.toHaveProperty("derivation_id");
    expect(navigate).toHaveBeenCalledWith("/runs/run-derive-1");
  });

  it("confirms before closing an unsaved trial response", async () => {
    vi.mocked(agents.get).mockResolvedValue(makeDetail());
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "Run agent" }));
    await user.click(screen.getByRole("button", { name: "Simulate response" }));
    await user.click(screen.getByRole("button", { name: "Close" }));

    const confirm = screen.getByRole("dialog", { name: "Discard unsaved response?" });
    expect(screen.getByRole("dialog", { name: "Run agent" })).toBeInTheDocument();
    await user.click(within(confirm).getByRole("button", { name: "Discard response" }));
    expect(screen.queryByRole("dialog", { name: "Run agent" })).toBeNull();
  });

  it("confirms before replacing an unsaved trial with a dataset run", async () => {
    vi.mocked(agents.get).mockResolvedValue(makeDetail());
    vi.mocked(runs.create).mockResolvedValue(makeRun());
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "Run agent" }));
    await user.click(screen.getByRole("button", { name: "Simulate response" }));
    await user.click(screen.getByRole("tab", { name: "Run a dataset" }));
    await user.click(screen.getByRole("button", { name: "Run dataset" }));

    expect(runs.create).not.toHaveBeenCalled();
    const confirm = screen.getByRole("dialog", { name: "Discard unsaved response?" });
    expect(within(confirm).getByText("Running a dataset will discard the unsaved response.")).toBeInTheDocument();
    await user.click(within(confirm).getByRole("button", { name: "Discard response" }));
    await waitFor(() => expect(runs.create).toHaveBeenCalledOnce());
  });

  it("disables a whole-dataset run when the agent has no selected version", async () => {
    vi.mocked(agents.get).mockResolvedValue(
      makeDetail({
        agent: {
          ...makeDetail().agent,
          active_version_id: null,
          version_count: 0,
        },
        versions: [],
      }),
    );
    renderDetail();

    await screen.findByRole("heading", { name: "Support agent" });
    expect(
      screen.getByRole("button", { name: "Run agent" }),
    ).toBeDisabled();
  });

  it("shows a rejected derive run's API message instead of navigating", async () => {
    vi.mocked(agents.get).mockResolvedValue(makeDetail());
    vi.mocked(runs.create).mockRejectedValue(
      new ApiError(
        "Dataset is missing required columns: context",
        "ContractError",
        422,
      ),
    );
    const user = userEvent.setup();
    renderDetail();

    await screen.findByRole("heading", { name: "Support agent" });
    await user.click(
      screen.getByRole("button", { name: "Run agent" }),
    );
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("tab", { name: "Run a dataset" }));
    await user.selectOptions(
      within(dialog).getByLabelText("Dataset"),
      "dataset-1",
    );
    await user.click(within(dialog).getByRole("button", { name: "Run dataset" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Dataset is missing required columns: context",
    );
    expect(
      within(dialog).getByRole("button", { name: "Run dataset" }),
    ).not.toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Close" }),
    ).not.toBeDisabled();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("allows only one derive run request while submission is pending", async () => {
    vi.mocked(agents.get).mockResolvedValue(makeDetail());
    let resolveRun!: (run: Run) => void;
    vi.mocked(runs.create).mockImplementation(
      () => new Promise((resolve) => (resolveRun = resolve)),
    );
    const user = userEvent.setup();
    renderDetail();

    await screen.findByRole("heading", { name: "Support agent" });
    await user.click(
      screen.getByRole("button", { name: "Run agent" }),
    );
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("tab", { name: "Run a dataset" }));
    const submit = within(dialog).getByRole("button", { name: "Run dataset" });

    await user.click(submit);
    await user.click(submit);

    expect(runs.create).toHaveBeenCalledOnce();
    expect(submit).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeDisabled();
    expect(within(dialog).getByRole("status", { name: "Loading" })).toBeInTheDocument();

    resolveRun(makeRun());
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/runs/run-derive-1"));
  });

  it("loads the active version and renders its bindings, raw spec, and response columns", async () => {
    vi.mocked(agents.get).mockResolvedValue(makeDetail());
    renderDetail();

    expect(
      await screen.findByRole("heading", { name: "Support agent" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Answers customer questions.")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Version" })).toHaveValue(
      "av-1",
    );
    expect(screen.getByLabelText("Version name")).toHaveValue("initial");
    expect(screen.getByLabelText("Notes")).toHaveValue("First revision.");
    expect(screen.getByLabelText("Model")).toHaveValue("gateway/openai:gpt-4o");
    expect(screen.getByLabelText("Prompt template")).toHaveValue(
      "Question: {question}",
    );
    expect(screen.getByLabelText("Required columns")).toHaveValue(
      "question, context",
    );
    expect(screen.getByLabelText("Spec")).toHaveValue(
      JSON.stringify(
        { name: "support-agent", instructions: ["Help the user."] },
        null,
        2,
      ),
    );
    expect(screen.getByText("answer")).toBeInTheDocument();
    expect(screen.getByText("confidence")).toBeInTheDocument();
    expect(screen.queryByTestId("trial-panel")).toBeNull();
  });

  it("switches the editor and trial panel when another version is selected", async () => {
    vi.mocked(agents.get).mockResolvedValue(
      makeDetail({
        versions: [
          makeVersion(),
          makeVersion({
            id: "av-2",
            version_name: "revised",
            notes: "Uses account context.",
            prompt_template: "Account: {account}",
            required_columns: ["account"],
          }),
        ],
      }),
    );
    const user = userEvent.setup();
    renderDetail();

    await screen.findByLabelText("Version name");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Version" }),
      "av-2",
    );

    expect(screen.getByLabelText("Version name")).toHaveValue("revised");
    expect(screen.getByLabelText("Notes")).toHaveValue("Uses account context.");
    expect(screen.getByLabelText("Prompt template")).toHaveValue(
      "Account: {account}",
    );
    expect(screen.getByLabelText("Required columns")).toHaveValue("account");
    await user.click(screen.getByRole("button", { name: "Run agent" }));
    expect(screen.getByTestId("trial-panel")).toHaveTextContent("av-2");
  });

  it("shows an inline JSON error, disables Save, and hides trials for an invalid spec", async () => {
    vi.mocked(agents.get).mockResolvedValue(makeDetail());
    const user = userEvent.setup();
    renderDetail();

    const spec = await screen.findByLabelText("Spec");
    await user.clear(spec);
    await user.type(spec, "not json");

    expect(await screen.findByText(/valid JSON/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.queryByTestId("trial-panel")).not.toBeInTheDocument();
  });

  it("patches only the binding field that changed", async () => {
    const updated = makeVersion({
      prompt_template: "New question: {question}",
    });
    vi.mocked(agents.get).mockResolvedValue(makeDetail());
    vi.mocked(agents.updateVersion).mockResolvedValue(updated);
    const user = userEvent.setup();
    renderDetail();

    const prompt = await screen.findByLabelText("Prompt template");
    await user.clear(prompt);
    fireEvent.change(prompt, { target: { value: "New question: {question}" } });
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(agents.updateVersion).toHaveBeenCalledWith("av-1", {
        prompt_template: "New question: {question}",
      }),
    );
  });

  it("edits and backspaces the prompt normally", async () => {
    vi.mocked(agents.get).mockResolvedValue(makeDetail());
    const user = userEvent.setup();
    renderDetail();

    const prompt = await screen.findByLabelText("Prompt template");
    await user.clear(prompt);
    await user.type(prompt, "abc");
    await user.keyboard("{Backspace}");
    expect(prompt).toHaveValue("ab");
  });

  it("edits instructions and harness capabilities within the agent spec", async () => {
    vi.mocked(api).mockResolvedValue({
      models: ["gateway/openai:gpt-4o"],
      default_model: "gateway/openai:gpt-4o",
      tools: [],
      capabilities: ["Planning", "CodeMode", "SubAgents"],
    });
    vi.mocked(agents.get).mockResolvedValue(makeDetail());
    vi.mocked(agents.updateVersion).mockResolvedValue(makeVersion());
    const user = userEvent.setup();
    renderDetail();

    const instructions = await screen.findByLabelText("Instructions");
    await user.clear(instructions);
    await user.type(instructions, "Plan before answering.");
    await user.click(screen.getByRole("checkbox", { name: "Planning" }));
    expect(screen.queryByRole("checkbox", { name: "SubAgents" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(agents.updateVersion).toHaveBeenCalledWith(
      "av-1",
      expect.objectContaining({
        spec: expect.objectContaining({
          instructions: "Plan before answering.",
          capabilities: [{ Planning: {} }],
        }),
      }),
    ));
  });

  it("starts evaluator generation from the selected agent contract", async () => {
    vi.mocked(agents.get).mockResolvedValue(makeDetail());
    const generated = { name: "Support judge" } as GeneratedConfig;
    vi.mocked(evaluators.generate).mockResolvedValue(generated);
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "Create evaluator" }));
    const dialog = screen.getByRole("dialog", { name: "Generate evaluator from agent" });
    await user.type(within(dialog).getByLabelText("Criteria"), "Helpful response");
    await user.click(within(dialog).getByRole("button", { name: "Generate evaluator" }));

    await waitFor(() => expect(evaluators.generate).toHaveBeenCalledWith(
      expect.objectContaining({
        criteria: "Helpful response",
        agent_version_id: "av-1",
        columns: ["question", "context", "answer", "confidence"],
      }),
    ));
    expect(navigate).toHaveBeenCalledWith("/evaluators", { state: { draft: generated } });
  });

  it("allows adding another dependency-to-column mapping", async () => {
    vi.mocked(agents.get).mockResolvedValue(makeDetail());
    vi.mocked(agents.updateVersion).mockResolvedValue(
      makeVersion({
        deps_mapping: { account_id: "account", locale: "language" },
      }),
    );
    const user = userEvent.setup();
    renderDetail();

    await screen.findByLabelText("Dependency 1");
    await user.click(
      screen.getByRole("button", { name: "Add dependency mapping" }),
    );

    expect(screen.getByLabelText("Dependency 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Column 2")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Dependency 2"), "locale");
    await user.type(screen.getByLabelText("Column 2"), "language");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(agents.updateVersion).toHaveBeenCalledWith("av-1", {
        deps_mapping: { account_id: "account", locale: "language" },
      }),
    );
  });

  it("creates a new version from the editable draft without mutating the selected version", async () => {
    const created = makeVersion({ id: "av-2", version_name: "new draft" });
    vi.mocked(agents.get)
      .mockResolvedValueOnce(makeDetail())
      .mockResolvedValueOnce(
        makeDetail({
          agent: {
            ...makeDetail().agent,
            active_version_id: "av-2",
            version_count: 2,
          },
          versions: [makeVersion(), created],
        }),
      );
    vi.mocked(agents.createVersion).mockResolvedValue(created);
    const user = userEvent.setup();
    renderDetail();

    await screen.findByRole("button", { name: "New version" });
    await user.click(screen.getByRole("button", { name: "New version" }));
    const name = screen.getByLabelText("Version name");
    await user.clear(name);
    await user.type(name, "new draft");
    await user.click(screen.getByRole("button", { name: "Create version" }));

    await waitFor(() =>
      expect(agents.createVersion).toHaveBeenCalledWith("agent-1", {
        version_name: "new draft",
        notes: "First revision.",
        model: "gateway/openai:gpt-4o",
        prompt_template: "Question: {question}",
        required_columns: ["question", "context"],
        deps_mapping: { account_id: "account" },
        spec: { name: "support-agent", instructions: ["Help the user."] },
      }),
    );
    expect(agents.updateVersion).not.toHaveBeenCalled();
  });

  it("renders a frozen version as read-only and offers Copy instead of Save", async () => {
    vi.mocked(agents.get).mockResolvedValue(
      makeDetail({ versions: [makeVersion({ frozen: true })] }),
    );
    renderDetail();

    await screen.findByLabelText("Version name");
    for (const label of [
      "Version name",
      "Notes",
      "Prompt template",
      "Required columns",
      "Spec",
    ]) {
      expect(screen.getByLabelText(label)).toHaveAttribute("readonly");
    }
    expect(screen.getByLabelText("Model")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Save" }),
    ).not.toBeInTheDocument();
  });

  it("does not remove a version until Delete is confirmed", async () => {
    vi.mocked(agents.get).mockResolvedValue(makeDetail());
    vi.mocked(agents.removeVersion).mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderDetail();

    await screen.findByRole("button", { name: "Delete" });
    await user.click(screen.getByRole("button", { name: "Delete" }));

    expect(agents.removeVersion).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(agents.removeVersion).toHaveBeenCalledWith("av-1"),
    );
  });

  it("imports a spec into the draft without creating a version", async () => {
    const importedSpec = { name: "imported", metadata: { source: "yaml" } };
    vi.mocked(agents.get).mockResolvedValue(makeDetail());
    vi.mocked(agents.importSpec).mockResolvedValue({
      spec: importedSpec,
      model: "local/codex",
      prompt_template: "Imported: {input}",
      required_columns: ["input"],
      deps_mapping: { user: "customer" },
    });
    const user = userEvent.setup();
    renderDetail();

    await screen.findByRole("button", { name: "Import" });
    await user.click(screen.getByRole("button", { name: "Import" }));
    await user.type(screen.getByLabelText("Import spec"), "name: imported");
    await user.click(screen.getByRole("button", { name: "Import spec" }));

    await waitFor(() =>
      expect(agents.importSpec).toHaveBeenCalledWith("name: imported", "yaml"),
    );
    expect(screen.getByLabelText("Spec")).toHaveValue(
      JSON.stringify(importedSpec, null, 2),
    );
    expect(screen.getByLabelText("Model")).toHaveValue("local/codex");
    expect(screen.getByLabelText("Prompt template")).toHaveValue(
      "Imported: {input}",
    );
    expect(agents.createVersion).not.toHaveBeenCalled();
  });

  it("passes the selected version to the trial panel", async () => {
    const second = makeVersion({ id: "av-2", version_name: "second" });
    vi.mocked(agents.get).mockResolvedValue(
      makeDetail({ versions: [makeVersion(), second] }),
    );
    const user = userEvent.setup();
    renderDetail();

    await screen.findByRole("button", { name: "Run agent" });
    await user.click(screen.getByRole("button", { name: "Run agent" }));
    expect(screen.getByTestId("trial-panel")).toHaveTextContent("av-1");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Version" }),
      "av-2",
    );
    expect(screen.getByTestId("trial-panel")).toHaveTextContent("av-2");
  });

  it("keeps the selected version's trial panel available while drafting a new version", async () => {
    vi.mocked(agents.get).mockResolvedValue(makeDetail());
    const user = userEvent.setup();
    renderDetail();

    await screen.findByRole("button", { name: "Run agent" });
    await user.click(screen.getByRole("button", { name: "Run agent" }));
    expect(screen.getByTestId("trial-panel")).toHaveTextContent("av-1");
    await user.click(screen.getByRole("button", { name: "New version" }));

    expect(
      screen.getByRole("button", { name: "Create version" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("trial-panel")).toHaveTextContent("av-1");
  });
  // A freshly created agent has no versions at all. Regression guard: the page used to
  // fall through to a null ErrorBanner and render nothing, stranding the new agent.
  it("opens a blank first-version draft when the agent has no versions", async () => {
    vi.mocked(agents.get).mockResolvedValue(
      makeDetail({
        agent: {
          id: "agent-1",
          created_at: "2026-09-20T00:00:00Z",
          name: "Support agent",
          description: "Answers customer questions.",
          active_version_id: null,
          version_count: 0,
        },
        versions: [],
      }),
    );
    renderDetail();

    expect(
      await screen.findByRole("heading", { name: "Support agent" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create version" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Prompt template")).toHaveValue("");
    expect(screen.getByLabelText("Required columns")).toHaveValue("");
    expect(screen.getByLabelText("Spec")).toHaveValue("{}");
    // Nothing exists to pick between, export, or trial yet.
    expect(screen.queryByRole("combobox", { name: "Version" })).toBeNull();
    expect(screen.queryByTestId("trial-panel")).toBeNull();
  });

  it("seeds the blank first-version draft with the configured default model", async () => {
    vi.mocked(agents.get).mockResolvedValue(
      makeDetail({
        versions: [],
        agent: {
          ...makeDetail().agent,
          active_version_id: null,
          version_count: 0,
        },
      }),
    );
    renderDetail();

    await waitFor(() =>
      expect(screen.getByLabelText("Model")).toHaveValue(
        "gateway/openai:gpt-4o",
      ),
    );
  });

  it("shows local models when the gateway key is absent", async () => {
    vi.mocked(setup.get).mockResolvedValue({
      keys: [{ name: "gateway_api_key", set: false }],
      local_cli_default: "codex",
      local_cli_options: ["claude", "codex"],
    } as SetupStatus);
    vi.mocked(agents.get).mockResolvedValue(makeDetail({
      versions: [],
      agent: { ...makeDetail().agent, active_version_id: null, version_count: 0 },
    }));
    renderDetail();

    const model = await screen.findByRole("combobox", { name: "Model" });
    await waitFor(() => expect(model).toHaveValue("local/codex"));
    expect(within(model).getByRole("option", { name: "local/claude" })).toBeInTheDocument();
    expect(within(model).queryByRole("option", { name: "gateway/openai:gpt-4o" })).toBeNull();
  });

  it("creates the first version from the blank draft", async () => {
    vi.mocked(agents.get).mockResolvedValue(
      makeDetail({
        versions: [],
        agent: {
          ...makeDetail().agent,
          active_version_id: null,
          version_count: 0,
        },
      }),
    );
    vi.mocked(agents.createVersion).mockResolvedValue(makeVersion());
    const user = userEvent.setup();
    renderDetail();

    await waitFor(() =>
      expect(screen.getByLabelText("Model")).toHaveValue(
        "gateway/openai:gpt-4o",
      ),
    );
    await user.clear(screen.getByLabelText("Version name"));
    await user.type(screen.getByLabelText("Version name"), "initial");
    await user.click(screen.getByRole("button", { name: "Create version" }));

    await waitFor(() =>
      expect(agents.createVersion).toHaveBeenCalledWith(
        "agent-1",
        expect.objectContaining({
          version_name: "initial",
          model: "gateway/openai:gpt-4o",
          spec: {},
          prompt_template: "",
          required_columns: [],
        }),
      ),
    );
  });

  it("puts Logfire sync in the top action bar beside the existing actions", async () => {
    vi.mocked(agents.get).mockResolvedValue(makeDetail());
    renderDetail();

    await screen.findByRole("heading", { name: "Support agent" });
    for (const name of ["Logfire sync", "Run agent", "Create evaluator", "Import", "Export"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(screen.queryByRole("dialog", { name: "Logfire sync" })).not.toBeInTheDocument();
  });

  it("opens and closes the sync dialog for the current agent", async () => {
    vi.mocked(agents.get).mockResolvedValue(makeDetail());
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "Logfire sync" }));
    expect(screen.getByRole("dialog", { name: "Logfire sync" })).toHaveTextContent("agent-1");
    await user.click(screen.getByRole("button", { name: "Close sync" }));
    expect(screen.queryByRole("dialog", { name: "Logfire sync" })).not.toBeInTheDocument();
  });

  it("does not call any sync write API on page load or when opening the dialog", async () => {
    vi.mocked(agents.get).mockResolvedValue(makeDetail());
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "Logfire sync" }));
    for (const fn of [
      agents.promptSyncLink,
      agents.promptSyncPull,
      agents.promptSyncPush,
      agents.promptSyncResolve,
      agents.promptSyncUnlink,
    ]) {
      expect(fn).not.toHaveBeenCalled();
    }
  });

  it("does not silently push to Logfire when a local version is saved", async () => {
    vi.mocked(agents.get).mockResolvedValue(makeDetail());
    vi.mocked(agents.updateVersion).mockResolvedValue(makeVersion());
    const user = userEvent.setup();
    renderDetail();

    const prompt = await screen.findByLabelText("Prompt template");
    await user.clear(prompt);
    fireEvent.change(prompt, { target: { value: "Changed: {question}" } });
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(agents.updateVersion).toHaveBeenCalled());
    expect(agents.promptSyncPush).not.toHaveBeenCalled();
    expect(agents.promptSyncStatus).not.toHaveBeenCalled();
  });

  it("reloads the agent and selects the new active version after a pull", async () => {
    const pulled = makeVersion({
      id: "av-2",
      version_name: "pulled",
      prompt_template: "Remote: {question}",
    });
    vi.mocked(agents.get)
      .mockResolvedValueOnce(makeDetail())
      .mockResolvedValue(
        makeDetail({
          agent: { ...makeDetail().agent, active_version_id: "av-2", version_count: 2 },
          versions: [makeVersion(), pulled],
        }),
      );
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "Logfire sync" }));
    await user.click(screen.getByRole("button", { name: "Simulate pull" }));

    await waitFor(() => expect(agents.get).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByLabelText("Version")).toHaveValue("av-2"));
    expect(screen.getByLabelText("Prompt template")).toHaveValue("Remote: {question}");
  });

  it("keeps the run side panel working alongside the sync action", async () => {
    vi.mocked(agents.get).mockResolvedValue(makeDetail());
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "Run agent" }));
    expect(screen.getByRole("dialog", { name: "Run agent" }).classList.contains("modal-side")).toBe(true);
    expect(screen.getByTestId("trial-panel")).toHaveTextContent("av-1");
  });
});
