import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Modal } from "./ui";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Modal", () => {
  it("does not render its dialog when closed", () => {
    render(
      <Modal open={false} onClose={vi.fn()}>
        <p>Body</p>
      </Modal>,
    );

    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("wires aria-labelledby to the title when one is given", () => {
    render(
      <Modal open title="Delete evaluator" onClose={vi.fn()}>
        <p>Body</p>
      </Modal>,
    );

    const dialog = screen.getByRole("dialog");
    const labelledBy = dialog.getAttribute("aria-labelledby");
    expect(labelledBy).toBeTruthy();
    const title = screen.getByText("Delete evaluator");
    expect(title.getAttribute("id")).toBe(labelledBy);
  });

  it("renders the description and points aria-describedby at it", () => {
    render(
      <Modal open title="Export" description="Choose a format to download." onClose={vi.fn()}>
        <p>Body</p>
      </Modal>,
    );

    const dialog = screen.getByRole("dialog");
    const describedBy = dialog.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    const description = screen.getByText("Choose a format to download.");
    expect(description.getAttribute("id")).toBe(describedBy);
    expect(description.className).toContain("modal-description");
  });

  it("omits aria-describedby when no description is provided", () => {
    render(
      <Modal open title="Export" onClose={vi.fn()}>
        <p>Body</p>
      </Modal>,
    );

    expect(screen.getByRole("dialog").getAttribute("aria-describedby")).toBeNull();
  });

  it("renders the footer after the body when provided", () => {
    const { container } = render(
      <Modal open title="Export" footer={<button type="button">Download</button>} onClose={vi.fn()}>
        <p>Body content</p>
      </Modal>,
    );

    const body = container.querySelector(".modal-body");
    const footer = container.querySelector(".modal-footer");
    expect(body).not.toBeNull();
    expect(footer).not.toBeNull();
    expect(screen.getByRole("button", { name: "Download" })).toBeTruthy();
    // The footer must sit after the body in document order.
    const position = body!.compareDocumentPosition(footer!);
    expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("renders no footer element when the footer prop is omitted", () => {
    const { container } = render(
      <Modal open title="Export" onClose={vi.fn()}>
        <p>Body content</p>
      </Modal>,
    );

    expect(container.querySelector(".modal-footer")).toBeNull();
  });

  it("defaults to the md size class", () => {
    render(
      <Modal open title="Export" onClose={vi.fn()}>
        <p>Body</p>
      </Modal>,
    );

    const dialog = screen.getByRole("dialog");
    expect(dialog.className).toContain("modal-md");
    expect(dialog.className).not.toContain("modal-sm");
    expect(dialog.className).not.toContain("modal-lg");
  });

  it("applies the matching class for each explicit size", () => {
    const sizes = [
      { size: "sm", cls: "modal-sm" },
      { size: "md", cls: "modal-md" },
      { size: "lg", cls: "modal-lg" },
    ] as const;

    for (const { size, cls } of sizes) {
      const { unmount } = render(
        <Modal open title="Export" size={size} onClose={vi.fn()}>
          <p>Body</p>
        </Modal>,
      );
      expect(screen.getByRole("dialog").className).toContain(cls);
      unmount();
    }
  });

  it("invokes onClose when Escape is pressed while open", () => {
    const onClose = vi.fn();
    render(
      <Modal open title="Export" onClose={onClose}>
        <p>Body</p>
      </Modal>,
    );

    fireEvent.keyDown(document, { key: "Escape" });

    expect(onClose).toHaveBeenCalledOnce();
  });

  it("removes the Escape listener on unmount", () => {
    const onClose = vi.fn();
    const { unmount } = render(
      <Modal open title="Export" onClose={onClose}>
        <p>Body</p>
      </Modal>,
    );

    unmount();
    fireEvent.keyDown(document, { key: "Escape" });

    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes on a backdrop click", async () => {
    const onClose = vi.fn();
    const { container } = render(
      <Modal open title="Export" onClose={onClose}>
        <p>Body</p>
      </Modal>,
    );

    const backdrop = container.querySelector(".modal-backdrop");
    expect(backdrop).not.toBeNull();
    await userEvent.click(backdrop as Element);

    expect(onClose).toHaveBeenCalledOnce();
  });

  it("does not close on a click inside the dialog", async () => {
    const onClose = vi.fn();
    render(
      <Modal open title="Export" onClose={onClose}>
        <p>Body</p>
      </Modal>,
    );

    await userEvent.click(screen.getByText("Body"));

    expect(onClose).not.toHaveBeenCalled();
  });
});
