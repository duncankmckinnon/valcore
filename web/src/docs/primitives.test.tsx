// The four primitives every content file is built from. Content prose is covered by
// the registry smoke render; what needs real assertions is the behavior these carry:
// a Copy button that puts the exact command on the clipboard, and links that resolve
// to real in-app routes rather than dead anchors.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import {
  CodeBlock,
  DocLink,
  DocNote,
  DocPage,
  DocSection,
  ExternalLink,
} from "./primitives";

afterEach(() => {
  cleanup();
});

describe("DocPage", () => {
  it("renders its children", () => {
    render(<DocPage>body text</DocPage>);

    expect(screen.getByText("body text")).toBeTruthy();
  });
});

describe("DocSection", () => {
  it("titles the block with an h2, leaving h1 to the page header", () => {
    render(<DocSection title="Versions">how versions freeze</DocSection>);

    // Level 2, not 1: DocsPage owns the single <h1> via PageHeader, so a section
    // heading that claimed h1 would give the page two.
    expect(screen.getByRole("heading", { level: 2, name: "Versions" })).toBeTruthy();
    expect(screen.getByText("how versions freeze")).toBeTruthy();
  });
});

describe("CodeBlock", () => {
  it("shows the command text", () => {
    render(<CodeBlock>valcore run my-eval my-dataset</CodeBlock>);

    expect(screen.getByText("valcore run my-eval my-dataset")).toBeTruthy();
  });

  it("copies the command to the clipboard", async () => {
    const user = userEvent.setup();

    // userEvent.setup() installs its own clipboard stub, so the spy must be installed
    // after it runs, matching ExportModal.test.tsx's copy-testing convention.
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
      writable: true,
    });

    render(<CodeBlock>valcore serve</CodeBlock>);
    await user.click(screen.getByRole("button", { name: "Copy" }));

    // The exact string, not a trimmed or re-indented variant: a command that does not
    // paste verbatim is worse than no Copy button.
    expect(writeText).toHaveBeenCalledWith("valcore serve");
  });
});

describe("DocNote", () => {
  it("renders its children and hides the icon from assistive tech", () => {
    const { container } = render(<DocNote>labels are only needed for validation</DocNote>);

    expect(screen.getByText("labels are only needed for validation")).toBeTruthy();
    expect(container.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  });
});

describe("ExternalLink", () => {
  it("renders a real anchor to the destination", () => {
    // Not a router Link: react-router would treat the URL as an in-app path and route
    // to a 404 shell instead of leaving the app.
    render(<ExternalLink href="https://ai.pydantic.dev/gateway/">gateway docs</ExternalLink>);

    expect(screen.getByRole("link", { name: "gateway docs" }).getAttribute("href")).toBe(
      "https://ai.pydantic.dev/gateway/",
    );
  });

  it("opens in a new tab without leaking the opener", () => {
    render(<ExternalLink href="https://logfire.pydantic.dev/">logfire</ExternalLink>);

    const link = screen.getByRole("link", { name: "logfire" });
    // The app is a local workspace with unsaved editor state; navigating away in the
    // same tab would discard it.
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noreferrer");
  });
});

describe("DocLink", () => {
  it("links to an in-app route", () => {
    render(
      <MemoryRouter>
        <DocLink to="/evaluators">Evaluators</DocLink>
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "Evaluators" }).getAttribute("href")).toBe(
      "/evaluators",
    );
  });
});
