// Route-table coverage for app-owned screens. Documentation lives on e-valcore.com;
// the legacy routes are covered through the URL mapping unit tests.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "./App";
import { setup } from "./api/client";

vi.mock("./pages/AgentsPage", () => ({
  default: () => <h1>Agents page</h1>,
}));

vi.mock("./api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/client")>();
  return {
    ...actual,
    setup: { ...actual.setup, get: vi.fn() },
  };
});

const setupGet = vi.mocked(setup.get);

afterEach(() => {
  cleanup();
});

function renderApp(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App routes", () => {
  it.each(["/agents", "/agents/agent-42"])("serves AgentsPage at %s", async (path) => {
    renderApp(path);

    expect(await screen.findByRole("heading", { level: 1, name: "Agents page" })).toBeTruthy();
  });

  it("serves Settings at /settings", async () => {
    setupGet.mockResolvedValue({
      keys: [],
      default_model: "gateway/anthropic:claude-sonnet-5",
      local_cli_default: null,
      local_cli_options: ["claude", "codex", "cursor"],
      logfire_explore_url: null,
      logfire_traces_url: null,
      logfire_datasets_url: null,
      logfire_frontend: { trace_url: null, token_set: false, session_replay: false },
    });
    renderApp("/settings");

    expect((await screen.findByRole("heading", { level: 1 })).textContent).toBe("Settings");
    expect(screen.getByRole("link", { name: "Settings" }).getAttribute("href")).toBe(
      "/settings",
    );
  });
});
