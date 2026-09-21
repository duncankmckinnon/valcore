// The agent detail page binds stored agent versions to the raw-spec editor and trial
// panel. These tests keep the page wiring separate from AgentTrialPanel's own behavior.

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AgentDetail from "./AgentDetail";
import { agents } from "../api/client";
import type {
  AgentDetail as AgentDetailData,
  AgentVersion,
} from "../api/types";

vi.mock("../components/AgentTrialPanel", () => ({
  default: ({ version }: { version: AgentVersion }) => (
    <div data-testid="trial-panel">Trial panel for {version.id}</div>
  ),
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
    },
  };
});

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

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AgentDetail", () => {
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
    expect(screen.getByTestId("trial-panel")).toHaveTextContent("av-1");
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
    await user.type(prompt, "New question: {question}");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(agents.updateVersion).toHaveBeenCalledWith("av-1", {
        prompt_template: "New question: {question}",
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
      "Model",
      "Prompt template",
      "Required columns",
      "Spec",
    ]) {
      expect(screen.getByLabelText(label)).toHaveAttribute("readonly");
    }
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

    expect(await screen.findByTestId("trial-panel")).toHaveTextContent("av-1");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Version" }),
      "av-2",
    );
    expect(screen.getByTestId("trial-panel")).toHaveTextContent("av-2");
  });
});
