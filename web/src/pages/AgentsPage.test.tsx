import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import AgentsPage from "./AgentsPage";
import { agents, ApiError } from "../api/client";
import type { AgentSummary } from "../api/types";

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
    },
  };
});

vi.mock("./AgentDetail", () => ({
  default: ({ agentId }: { agentId: string }) => <div>Agent detail: {agentId}</div>,
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

function renderPage(path = "/agents") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AgentsPage />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
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
    expect(screen.getByText("Answers customer questions.")).toBeTruthy();
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
    renderPage("/agents/agent-42");

    expect(screen.getByText("Agent detail: agent-42")).toBeTruthy();
    expect(agents.list).not.toHaveBeenCalled();
    expect(screen.queryByRole("heading", { name: "Agents" })).toBeNull();
  });
});
