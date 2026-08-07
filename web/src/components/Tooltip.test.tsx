import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Tooltip } from "./Tooltip";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Tooltip", () => {
  it("hides the popover until the trigger is clicked", () => {
    render(<Tooltip text="Runs depend on this evaluator." />);

    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("renders a type=button trigger with the default accessible name", () => {
    render(<Tooltip text="Helpful detail." />);

    const trigger = screen.getByRole("button", { name: "More information" });
    expect(trigger.getAttribute("type")).toBe("button");
  });

  it("uses the provided label as the trigger's accessible name", () => {
    render(<Tooltip text="Helpful detail." label="What is a blocker?" />);

    expect(screen.getByRole("button", { name: "What is a blocker?" })).toBeTruthy();
  });

  it("opens on click and exposes the text via role=tooltip", async () => {
    render(<Tooltip text="Helpful detail." />);

    await userEvent.click(screen.getByRole("button", { name: "More information" }));

    const popover = screen.getByRole("tooltip");
    expect(popover).toBeTruthy();
    expect(popover.textContent).toContain("Helpful detail.");
  });

  it("wires aria-describedby to the popover id only while open", async () => {
    render(<Tooltip text="Helpful detail." />);
    const trigger = screen.getByRole("button", { name: "More information" });

    expect(trigger.getAttribute("aria-describedby")).toBeNull();

    await userEvent.click(trigger);
    const popover = screen.getByRole("tooltip");
    expect(trigger.getAttribute("aria-describedby")).toBe(popover.getAttribute("id"));

    await userEvent.click(trigger);
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(trigger.getAttribute("aria-describedby")).toBeNull();
  });

  it("opens on pointer enter and closes on pointer leave", async () => {
    render(<Tooltip text="Helpful detail." />);
    const trigger = screen.getByRole("button", { name: "More information" });

    // userEvent.hover/unhover dispatch the pointerover/pointerout pair React
    // simulates onPointerEnter/onPointerLeave from — a bare pointerenter would not.
    await userEvent.hover(trigger);
    expect(screen.getByRole("tooltip")).toBeTruthy();

    await userEvent.unhover(trigger);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("closes when Escape is pressed", async () => {
    render(<Tooltip text="Helpful detail." />);

    await userEvent.click(screen.getByRole("button", { name: "More information" }));
    expect(screen.getByRole("tooltip")).toBeTruthy();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("closes on a click outside the wrapper", async () => {
    render(
      <div>
        <Tooltip text="Helpful detail." />
        <button type="button">Elsewhere</button>
      </div>,
    );

    await userEvent.click(screen.getByRole("button", { name: "More information" }));
    expect(screen.getByRole("tooltip")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Elsewhere" }));
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("closes on a second trigger click", async () => {
    render(<Tooltip text="Helpful detail." />);
    const trigger = screen.getByRole("button", { name: "More information" });

    await userEvent.click(trigger);
    expect(screen.getByRole("tooltip")).toBeTruthy();

    await userEvent.click(trigger);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });
});
