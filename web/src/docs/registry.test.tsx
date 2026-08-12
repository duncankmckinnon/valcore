// The registry is the single source for tab order, slugs, titles, and bodies, so the
// checks here are the ones that would otherwise fail at runtime as a blank tab: a
// duplicate slug (two tabs, one reachable), an empty title (an unlabelled tab), or a
// content file that throws (a blank pane under a working tab strip).
//
// A .tsx suite rather than .ts: every body may render a DocLink, which needs router
// context, so the smoke render wraps each body in a MemoryRouter.
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { DOCS, resolveDoc } from "./registry";

afterEach(() => {
  cleanup();
});

describe("docs registry", () => {
  it("lists the five documented tabs in order", () => {
    // Keys leads: nothing that calls a model runs without the gateway key, so it is
    // both the first thing a new user needs and the tab /docs lands on.
    expect(DOCS.map((entry) => entry.slug)).toEqual([
      "keys",
      "evals",
      "datasets",
      "runs",
      "cli",
    ]);
  });

  it("gives every entry a unique slug", () => {
    const slugs = DOCS.map((entry) => entry.slug);
    expect(new Set(slugs).size).toBe(slugs.length);
  });

  it("gives every entry a non-empty slug and title", () => {
    for (const entry of DOCS) {
      expect(entry.slug.length).toBeGreaterThan(0);
      expect(entry.title.length).toBeGreaterThan(0);
    }
  });

  it("resolves a known slug to its entry", () => {
    expect(resolveDoc("runs").title).toBe("Runs");
  });

  it("lands on Keys for the bare route", () => {
    expect(resolveDoc(undefined).title).toBe("Keys");
  });

  it("falls back to the first tab for an unknown slug", () => {
    // A renamed or mistyped slug must land on readable content rather than a blank
    // pane under a working tab strip.
    expect(resolveDoc("does-not-exist")).toBe(DOCS[0]);
  });

  it("falls back to the first tab when no slug is given", () => {
    // This is the bare /docs route, which carries no :slug param.
    expect(resolveDoc(undefined)).toBe(DOCS[0]);
  });

  it.each(DOCS.map((entry) => [entry.slug, entry] as const))(
    "%s renders its body without throwing",
    (_slug, entry) => {
      const { container } = render(
        <MemoryRouter>
          <entry.Body />
        </MemoryRouter>,
      );

      // Rendered *something*: an empty body is a blank tab, which the tab strip
      // would happily present as working.
      expect(container.textContent?.trim().length).toBeGreaterThan(0);
    },
  );
});
