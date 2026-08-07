import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { EmptyState } from "./EmptyState";
import { Table } from "./ui";

afterEach(() => {
  cleanup();
});

describe("EmptyState", () => {
  it("renders its message", () => {
    render(<EmptyState message="No evaluators yet." />);

    expect(screen.getByText("No evaluators yet.")).toBeTruthy();
  });

  it("renders the icon when given", () => {
    render(
      <EmptyState
        icon={<svg role="img" aria-label="empty box" />}
        message="No evaluators yet."
      />,
    );

    expect(screen.getByRole("img", { name: "empty box" })).toBeTruthy();
  });

  it("does not render an icon when none is given", () => {
    render(<EmptyState message="No evaluators yet." />);

    expect(screen.queryByRole("img")).toBeNull();
  });

  it("renders the action when given", () => {
    render(
      <EmptyState
        message="No evaluators yet."
        action={<button type="button">Create one</button>}
      />,
    );

    expect(screen.getByRole("button", { name: "Create one" })).toBeTruthy();
  });

  it("does not render an action when none is given", () => {
    render(<EmptyState message="No evaluators yet." />);

    expect(screen.queryByRole("button")).toBeNull();
  });

  it("accepts non-string message nodes", () => {
    render(<EmptyState message={<span>Nothing here.</span>} />);

    expect(screen.getByText("Nothing here.")).toBeTruthy();
  });
});

describe("EmptyState inside Table's empty prop", () => {
  type Row = { id: string; name: string };
  const columns = [{ header: "Name", cell: (row: Row) => row.name }];
  const rowKey = (row: Row) => row.id;

  it("renders the empty state when there are no rows", () => {
    render(
      <Table
        columns={columns}
        rows={[]}
        rowKey={rowKey}
        empty={<EmptyState message="No evaluators yet." />}
      />,
    );

    expect(screen.getByText("No evaluators yet.")).toBeTruthy();
    // With no rows, the table itself must not render.
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("does not render the empty state when rows exist", () => {
    render(
      <Table
        columns={columns}
        rows={[{ id: "1", name: "Faithfulness" }]}
        rowKey={rowKey}
        empty={<EmptyState message="No evaluators yet." />}
      />,
    );

    expect(screen.queryByText("No evaluators yet.")).toBeNull();
    expect(screen.getByText("Faithfulness")).toBeTruthy();
  });
});
