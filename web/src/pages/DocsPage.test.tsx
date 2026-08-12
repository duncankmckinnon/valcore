// DocsPage owns routing and chrome, not content, so these tests are about which entry
// is selected and how it is reachable — never about the prose, which registry.test.tsx
// covers.
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import DocsPage from "./DocsPage";
import { DOCS } from "../docs/registry";

afterEach(() => {
  cleanup();
});

// Both routes point at the same component, so the harness mounts both: the bare /docs
// path carries no :slug, which is its own resolution case.
function renderDocs(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/docs" element={<DocsPage />} />
        <Route path="/docs/:slug" element={<DocsPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

// Content prose links to other tabs and to working surfaces, so several body links share
// a name with a tab. Every tab assertion is scoped to the strip's <nav> to keep it about
// the tab and not about whatever the prose happens to link to.
function tab(name: string): HTMLElement {
  return within(screen.getByRole("navigation")).getByRole("link", { name });
}

describe("DocsPage tabs", () => {
  it("renders one tab per registry entry", () => {
    renderDocs("/docs");

    const tabs = within(screen.getByRole("navigation")).getAllByRole("link");
    expect(tabs.map((tab) => tab.textContent)).toEqual(DOCS.map((entry) => entry.title));
  });

  it("points each tab at its slug", () => {
    renderDocs("/docs");

    expect(tab("CLI").getAttribute("href")).toBe("/docs/cli");
  });

  it("marks the tab for the current slug as current", () => {
    renderDocs("/docs/runs");

    expect(tab("Runs").getAttribute("aria-current")).toBe("page");
    expect(tab("Evals").getAttribute("aria-current")).toBeNull();
  });

  it("marks the first tab as current on the bare /docs route", () => {
    renderDocs("/docs");

    // The tab strip must agree with the body. Left to URL matching, no tab is current
    // here — /docs/<first slug> does not match /docs — so the page renders the first
    // tab's body under an entirely unlit strip.
    //
    // Asserted against DOCS[0] rather than a hardcoded title so reordering the tabs is
    // a one-line change in the registry; registry.test.tsx is what pins the order.
    expect(tab(DOCS[0].title).getAttribute("aria-current")).toBe("page");
  });

  it("marks the first tab as current for an unknown slug", () => {
    renderDocs("/docs/nope");

    // Same rule, other fallback path: whichever body resolveDoc picked is the tab that
    // must look selected.
    expect(tab(DOCS[0].title).getAttribute("aria-current")).toBe("page");
  });

  it("navigates to another tab on click", async () => {
    const user = userEvent.setup();
    renderDocs("/docs");

    await user.click(tab("Datasets"));

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Datasets");
  });
});

describe("DocsPage body selection", () => {
  it("renders the first tab on the bare /docs route", () => {
    renderDocs("/docs");

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(DOCS[0].title);
  });

  it("renders the tab named by the slug", () => {
    renderDocs("/docs/cli");

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("CLI");
  });

  it("renders the first tab for an unknown slug instead of nothing", () => {
    renderDocs("/docs/nope");

    // The failure this guards: a stale bookmark rendering a tab strip over an empty
    // pane, which reads as a broken app rather than a bad link.
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(DOCS[0].title);
  });

  it("renders exactly one h1", () => {
    renderDocs("/docs/datasets");

    // PageHeader owns the page heading; a content file that minted its own would give
    // the page two and break the document outline.
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });
});
