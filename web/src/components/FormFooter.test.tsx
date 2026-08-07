import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { FormFooter } from "./FormFooter";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("FormFooter", () => {
  it("shows only the first blocker when several are given", () => {
    render(
      <FormFooter blockers={["Name is required.", "Pick at least one label."]}>
        <button type="button">Save</button>
      </FormFooter>,
    );

    const status = screen.getByRole("status");
    expect(status.textContent).toContain("Name is required.");
    expect(screen.queryByText("Pick at least one label.")).toBeNull();
  });

  it("renders the ready node when there are no blockers", () => {
    render(
      <FormFooter blockers={[]} ready={<span>Ready to save.</span>}>
        <button type="button">Save</button>
      </FormFooter>,
    );

    expect(screen.getByText("Ready to save.")).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("renders no status text with no blockers and no ready node", () => {
    render(
      <FormFooter blockers={[]}>
        <button type="button">Save</button>
      </FormFooter>,
    );

    expect(screen.queryByRole("status")).toBeNull();
  });

  it("always renders the action children regardless of blocker state", () => {
    const { rerender } = render(
      <FormFooter blockers={["Name is required."]}>
        <button type="button">Save</button>
      </FormFooter>,
    );
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();

    rerender(
      <FormFooter blockers={[]} ready={<span>Ready.</span>}>
        <button type="button">Save</button>
      </FormFooter>,
    );
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
  });

  it("does not disable its action children itself", () => {
    render(
      <FormFooter blockers={["Name is required."]}>
        <button type="button">Save</button>
      </FormFooter>,
    );

    expect(screen.getByRole("button", { name: "Save" })).not.toBeDisabled();
  });
});
