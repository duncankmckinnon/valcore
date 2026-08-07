import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { PageHeader } from "./PageHeader";

afterEach(() => {
  cleanup();
});

describe("PageHeader", () => {
  it("renders the title as a level-1 heading", () => {
    render(<PageHeader title="Evaluators" />);

    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.textContent).toBe("Evaluators");
  });

  it("renders the description when given", () => {
    render(
      <PageHeader title="Evaluators" description="Define and version your LLM judges." />,
    );

    expect(screen.getByText("Define and version your LLM judges.")).toBeTruthy();
  });

  it("does not render a description when none is given", () => {
    render(<PageHeader title="Evaluators" />);

    // Only the heading should carry text — no stray description paragraph.
    expect(screen.queryByText("Define and version your LLM judges.")).toBeNull();
  });

  it("renders the action when given", () => {
    render(
      <PageHeader
        title="Evaluators"
        action={<button type="button">New evaluator</button>}
      />,
    );

    expect(screen.getByRole("button", { name: "New evaluator" })).toBeTruthy();
  });

  it("does not render an action when none is given", () => {
    render(<PageHeader title="Evaluators" />);

    expect(screen.queryByRole("button")).toBeNull();
  });

  it("renders the action after the heading in document order", () => {
    render(
      <PageHeader
        title="Evaluators"
        description="Define and version your LLM judges."
        action={<button type="button">New evaluator</button>}
      />,
    );

    const heading = screen.getByRole("heading", { level: 1 });
    const action = screen.getByRole("button", { name: "New evaluator" });
    const position = heading.compareDocumentPosition(action);
    expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("accepts non-string title nodes", () => {
    render(<PageHeader title={<span>Runs</span>} />);

    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.textContent).toBe("Runs");
  });
});
