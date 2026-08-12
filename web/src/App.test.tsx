// Route-table coverage. DocsPage.test.tsx mounts the docs routes itself, which proves the
// component but not that App declares them — this file is what fails if /docs is missing
// from the real route table.
//
// Only docs paths are exercised: the other pages fetch on mount, and this suite is about
// wiring, not about mocking every endpoint.
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "./App";
import { DOCS } from "./docs/registry";

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
  it("serves the docs section at /docs", () => {
    renderApp("/docs");

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(DOCS[0].title);
  });

  it("serves a docs tab at /docs/:slug", () => {
    renderApp("/docs/runs");

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Runs");
  });

  it("renders the docs section inside the app shell", () => {
    renderApp("/docs");

    // Two navs: the sidebar and the tab strip. If docs were declared outside the layout
    // route, the sidebar would vanish while reading them.
    expect(screen.getAllByRole("navigation")).toHaveLength(2);
    expect(screen.getByRole("link", { name: "Docs" }).getAttribute("href")).toBe("/docs");
  });
});
