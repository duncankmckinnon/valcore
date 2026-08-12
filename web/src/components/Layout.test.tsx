import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import Layout from "./Layout";

afterEach(() => {
  cleanup();
});

// Layout renders an <Outlet/>, so it must sit inside a layout route. A wildcard
// child keeps every path renderable so NavLink active state resolves against the
// MemoryRouter's initial location rather than falling through to nothing.
function renderLayout(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="*" element={<Outlet />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("Layout nav", () => {
  it("renders every nav link with its accessible name", () => {
    renderLayout("/");

    // The icon span is aria-hidden, so each link's accessible name is the label
    // text alone.
    expect(screen.getByRole("link", { name: "Overview" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Evaluators" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Datasets" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Runs" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Compare" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Docs" })).toBeTruthy();
  });

  it("renders exactly the six expected nav links and nothing else", () => {
    renderLayout("/");

    // Guards against a stray link (e.g. the brand wordmark accidentally becoming
    // a link, or a footer/version badge that this task must not add).
    expect(screen.getAllByRole("link")).toHaveLength(6);
  });

  it("orders Docs directly under Overview, above the labelled groups", () => {
    renderLayout("/");

    // Docs sits with Overview as the two ungrouped entries at the top: it is read
    // before you have anything to author or measure, so it should not be buried under
    // the working surfaces.
    expect(screen.getAllByRole("link").map((link) => link.textContent)).toEqual([
      "Overview",
      "Docs",
      "Evaluators",
      "Datasets",
      "Runs",
      "Compare",
    ]);
  });

  it("points Docs at /docs", () => {
    renderLayout("/");

    expect(screen.getByRole("link", { name: "Docs" }).getAttribute("href")).toBe("/docs");
  });

  it("keeps Docs active on a docs sub-route", () => {
    renderLayout("/docs/datasets");

    // Docs deliberately omits `end`: every /docs/:slug tab must keep the nav item
    // lit, otherwise the sidebar goes blank-looking while reading any tab but the
    // first.
    expect(screen.getByRole("link", { name: "Docs" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "Overview" }).getAttribute("aria-current")).toBeNull();
  });

  it("points Overview at / and Compare at /runs/compare", () => {
    renderLayout("/");

    expect(screen.getByRole("link", { name: "Overview" }).getAttribute("href")).toBe("/");
    expect(screen.getByRole("link", { name: "Compare" }).getAttribute("href")).toBe(
      "/runs/compare",
    );
  });

  it("routes the other links to their sections", () => {
    renderLayout("/");

    expect(screen.getByRole("link", { name: "Evaluators" }).getAttribute("href")).toBe(
      "/evaluators",
    );
    expect(screen.getByRole("link", { name: "Datasets" }).getAttribute("href")).toBe("/datasets");
    expect(screen.getByRole("link", { name: "Runs" }).getAttribute("href")).toBe("/runs");
  });

  it("shows the Author and Measure section labels", () => {
    renderLayout("/");

    expect(screen.getByText("Author")).toBeTruthy();
    expect(screen.getByText("Measure")).toBeTruthy();
  });

  it("renders the valcore wordmark", () => {
    renderLayout("/");

    expect(screen.getByText("valcore")).toBeTruthy();
  });

  it("does not expose the brand image to assistive tech", () => {
    const { container } = renderLayout("/");

    // An empty alt removes the image from the accessibility tree; the adjacent
    // wordmark already names the brand.
    expect(screen.queryByRole("img")).toBeNull();

    const logo = container.querySelector("img");
    expect(logo).not.toBeNull();
    expect(logo?.getAttribute("alt")).toBe("");
  });

  it("marks only the active section on /datasets and never Overview", () => {
    renderLayout("/datasets");

    // NavLink sets aria-current="page" on the active link regardless of the
    // className callback, so active state is observable without touching CSS.
    expect(screen.getByRole("link", { name: "Datasets" }).getAttribute("aria-current")).toBe(
      "page",
    );

    // The end-prop regression: without `end` on the Overview link, "/" matches
    // every route and Overview stays permanently active.
    expect(screen.getByRole("link", { name: "Overview" }).getAttribute("aria-current")).toBeNull();
    expect(screen.getByRole("link", { name: "Evaluators" }).getAttribute("aria-current")).toBeNull();
    expect(screen.getByRole("link", { name: "Runs" }).getAttribute("aria-current")).toBeNull();
  });

  it("marks only Overview active on / (end prop)", () => {
    renderLayout("/");

    expect(screen.getByRole("link", { name: "Overview" }).getAttribute("aria-current")).toBe(
      "page",
    );
    expect(screen.getByRole("link", { name: "Datasets" }).getAttribute("aria-current")).toBeNull();
  });

  it("uses a single nav landmark for the sidebar", () => {
    renderLayout("/");

    // Section labels are presentational grouping, not landmarks — one <nav>.
    expect(screen.getAllByRole("navigation")).toHaveLength(1);
  });
});
