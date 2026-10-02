import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import AgentsPage from "./AgentsPage";
import { agents, ApiError } from "../api/client";
import { GATEWAY_BLOCKER, useSetup } from "../components/useSetup";
import type { UseSetupResult } from "../components/useSetup";
import type { AgentDraft, AgentSummary } from "../api/types";

const navigate = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    agents: {
      list: vi.fn(),
      create: vi.fn(),
      remove: vi.fn(),
      generate: vi.fn(),
    },
  };
});

// Only Generate (prompt mode) calls a model through the gateway; scratch-mode Create
// must stay usable regardless. The hook is mocked directly so each test can set
// gatewayReady without re-exercising useSetup's own fetch machinery.
vi.mock("../components/useSetup", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../components/useSetup")>();
  return { ...actual, useSetup: vi.fn() };
});

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

vi.mock("./AgentDetail", () => ({
  default: ({
    agentId,
    initialDraft,
  }: {
    agentId: string;
    initialDraft?: { version_name: string };
  }) => (
    <div>
      Agent detail: {agentId}
      {initialDraft ? ` (draft ${initialDraft.version_name})` : ""}
    </div>
  ),
}));

function makeAgent(overrides: Partial<AgentSummary> = {}): AgentSummary {
  return {
    id: "agent-1",
    created_at: "2026-09-20T00:00:00Z",
    name: "Support assistant",
    description: "Answers customer questions.",
    active_version_id: null,
    version_count: 1,
    ...overrides,
  };
}

function makeDraft(overrides: Partial<AgentDraft> = {}): AgentDraft {
  return {
    version_name: "v1",
    spec: { instructions: "Do the thing." },
    prompt_template: "{input}",
    required_columns: ["input"],
    deps_mapping: {},
    output_fields: [],
    rationale: "because",
    ...overrides,
  };
}

function renderPage(path: string | { pathname: string; state: unknown } = "/agents") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/agents" element={<AgentsPage />} />
        <Route path="/agents/:id" element={<AgentsPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

beforeEach(() => {
  useSetupMock.mockReturnValue(makeSetupResult());
});

describe("AgentsPage", () => {
  it("renders an explanatory empty state with a Create control", async () => {
    vi.mocked(agents.list).mockResolvedValue([]);
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText(/agent being measured/i)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Create agent" }));

    expect(screen.getByRole("dialog", { name: "New agent" })).toBeTruthy();
  });

  it("renders every agent with its description and version count", async () => {
    vi.mocked(agents.list).mockResolvedValue([
      makeAgent({ id: "agent-1", name: "Support assistant", version_count: 2 }),
      makeAgent({ id: "agent-2", name: "Research assistant", version_count: 7 }),
    ]);
    renderPage();

    expect(await screen.findByRole("link", { name: "Support assistant" })).toHaveAttribute(
      "href",
      "/agents/agent-1",
    );
    expect(screen.getByRole("link", { name: "Research assistant" })).toHaveAttribute(
      "href",
      "/agents/agent-2",
    );
    // Descriptions belong to each row, so identical copy must not silently disappear
    // from later agents.
    expect(screen.getAllByText("Answers customer questions.")).toHaveLength(2);
    expect(screen.getByRole("cell", { name: "2" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: "7" })).toBeTruthy();
  });

  it("creates an agent and opens its detail route", async () => {
    vi.mocked(agents.list).mockResolvedValue([]);
    vi.mocked(agents.create).mockResolvedValue(makeAgent({ id: "new-agent" }));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "New agent" }));
    await user.type(screen.getByLabelText("Agent name"), "Triage agent");
    await user.type(screen.getByLabelText("Description"), "Routes incoming requests.");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(agents.create).toHaveBeenCalledWith({
        name: "Triage agent",
        description: "Routes incoming requests.",
      }),
    );
    expect(navigate).toHaveBeenCalledWith("/agents/new-agent");
  });

  it("does not delete until its confirmation dialog is confirmed", async () => {
    const agent = makeAgent();
    vi.mocked(agents.list).mockResolvedValue([agent]);
    vi.mocked(agents.remove).mockResolvedValue();
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Delete Support assistant" }));

    expect(agents.remove).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(agents.remove).toHaveBeenCalledWith("agent-1"));
    await waitFor(() => expect(agents.list).toHaveBeenCalledTimes(2));
  });

  it("shows a ReferencedError message when deletion is blocked by derivations", async () => {
    vi.mocked(agents.list).mockResolvedValue([makeAgent()]);
    vi.mocked(agents.remove).mockRejectedValue(
      new ApiError(
        "Cannot delete Support assistant because saved derivations still reference it.",
        "ReferencedError",
        409,
      ),
    );
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Delete Support assistant" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));

    expect(
      await screen.findByText(
        "Cannot delete Support assistant because saved derivations still reference it.",
      ),
    ).toBeTruthy();
  });

  it("renders the detail page only when the route has an id", () => {
    renderPage({ pathname: "/agents/agent-42", state: { draft: { version_name: "v1" } } });

    expect(screen.getByText("Agent detail: agent-42 (draft v1)")).toBeTruthy();
    expect(agents.list).not.toHaveBeenCalled();
    expect(screen.queryByRole("heading", { name: "Agents" })).toBeNull();
  });

  it("generates a draft from a prompt, then creates the agent, and navigates with the draft", async () => {
    vi.mocked(agents.list).mockResolvedValue([]);
    const draft = makeDraft();
    vi.mocked(agents.generate).mockResolvedValue(draft);
    vi.mocked(agents.create).mockResolvedValue(makeAgent({ id: "new-agent" }));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "New agent" }));
    await user.click(screen.getByRole("tab", { name: "From prompt" }));

    await user.type(screen.getByLabelText("Agent name"), "Triage agent");
    await user.type(screen.getByLabelText("Description"), "Routes incoming requests.");
    await user.type(screen.getByLabelText("Prompt"), "Classify the ticket and route it.");
    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() =>
      expect(agents.create).toHaveBeenCalledWith({
        name: "Triage agent",
        description: "Routes incoming requests.",
      }),
    );
    expect(agents.generate).toHaveBeenCalledWith({
      prompt: "Classify the ticket and route it.",
    });

    const generateOrder = vi.mocked(agents.generate).mock.invocationCallOrder[0];
    const createOrder = vi.mocked(agents.create).mock.invocationCallOrder[0];
    expect(generateOrder).toBeLessThan(createOrder);

    expect(navigate).toHaveBeenCalledWith("/agents/new-agent", { state: { draft } });
  });

  it("shows the generation error and leaves no agent behind when generate fails", async () => {
    vi.mocked(agents.list).mockResolvedValue([]);
    vi.mocked(agents.generate).mockRejectedValue(new Error("model unavailable"));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "New agent" }));
    await user.click(screen.getByRole("tab", { name: "From prompt" }));

    await user.type(screen.getByLabelText("Agent name"), "Triage agent");
    await user.type(screen.getByLabelText("Description"), "Routes incoming requests.");
    await user.type(screen.getByLabelText("Prompt"), "Classify the ticket and route it.");
    await user.click(screen.getByRole("button", { name: "Generate" }));

    const dialog = screen.getByRole("dialog", { name: "New agent" });
    expect(await within(dialog).findByText("model unavailable")).toBeTruthy();
    expect(agents.create).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("disables Generate and shows the shared gateway blocker in prompt mode when not ready", async () => {
    useSetupMock.mockReturnValue(makeSetupResult({ gatewayReady: false }));
    vi.mocked(agents.list).mockResolvedValue([]);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "New agent" }));
    await user.click(screen.getByRole("tab", { name: "From prompt" }));

    await user.type(screen.getByLabelText("Agent name"), "Triage agent");
    await user.type(screen.getByLabelText("Prompt"), "Classify the ticket and route it.");

    expect(screen.getByText(GATEWAY_BLOCKER)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
    expect(agents.generate).not.toHaveBeenCalled();
  });
});
