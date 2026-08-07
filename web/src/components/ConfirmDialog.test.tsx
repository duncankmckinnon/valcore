import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfirmDialog } from "./ui";
import { ApiError } from "../api/client";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ConfirmDialog", () => {
  it("fires onConfirm when the confirm button is clicked", async () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Delete evaluator"
        message="This cannot be undone."
        onConfirm={onConfirm}
        onClose={vi.fn()}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("fires onClose when the cancel button is clicked", async () => {
    const onClose = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Delete evaluator"
        message="This cannot be undone."
        onConfirm={vi.fn()}
        onClose={onClose}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onClose).toHaveBeenCalledOnce();
  });

  it("uses the custom confirm label when provided", () => {
    render(
      <ConfirmDialog
        open
        title="Remove version"
        message="Remove it?"
        confirmLabel="Remove"
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Remove" })).toBeTruthy();
  });

  it("shows the run count and disables confirm on a ReferencedError", () => {
    const error = new ApiError("cannot delete", "ReferencedError", 409, { run_count: 4 });
    render(
      <ConfirmDialog
        open
        title="Delete evaluator"
        message="This cannot be undone."
        error={error}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("4 runs depend on this. Delete them first.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("does not swallow a non-referenced error", () => {
    const error = new ApiError("something broke", "ConfigError", 422);
    render(
      <ConfirmDialog
        open
        title="Delete evaluator"
        message="This cannot be undone."
        error={error}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("something broke")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete" })).not.toBeDisabled();
  });

  it("renders the confirm and cancel actions inside the modal footer", () => {
    const { container } = render(
      <ConfirmDialog
        open
        title="Delete evaluator"
        message="This cannot be undone."
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    const footer = container.querySelector(".modal-footer");
    expect(footer).not.toBeNull();
    // The actions moved out of the body into the footer slot.
    expect(container.querySelector(".modal-body .form-actions")).toBeNull();
    expect(footer!.querySelector("button")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Delete" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
  });
});
