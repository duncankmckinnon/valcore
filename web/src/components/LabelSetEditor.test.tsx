import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import LabelSetEditor from "./LabelSetEditor";
import type { LabelSetCreate } from "../api/types";

const BLANK: LabelSetCreate = {
  name: "",
  description: "",
  kind: "categorical",
  labels: [],
  minimum: null,
  maximum: null,
};

describe("LabelSetEditor", () => {
  it("adds a categorical label with a description", () => {
    const onChange = vi.fn();
    render(<LabelSetEditor value={BLANK} onChange={onChange} />);

    fireEvent.change(screen.getByLabelText("Label name"), { target: { value: "good" } });
    fireEvent.change(screen.getByLabelText("Label criteria"), {
      target: { value: "Meets the bar" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add label" }));

    expect(onChange).toHaveBeenCalledWith({
      ...BLANK,
      labels: [{ name: "good", description: "Meets the bar" }],
    });
  });

  it("removes a label", () => {
    const onChange = vi.fn();
    const value: LabelSetCreate = {
      ...BLANK,
      labels: [{ name: "good", description: "" }, { name: "bad", description: "" }],
    };
    render(<LabelSetEditor value={value} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: "Remove good" }));

    expect(onChange).toHaveBeenCalledWith({ ...value, labels: [{ name: "bad", description: "" }] });
  });

  it("switches to numeric bounds", () => {
    const onChange = vi.fn();
    render(<LabelSetEditor value={BLANK} onChange={onChange} />);

    fireEvent.change(screen.getByLabelText("Label kind"), { target: { value: "numeric" } });

    expect(onChange).toHaveBeenCalledWith({ ...BLANK, kind: "numeric", labels: null, minimum: 0, maximum: 1 });
  });

  it("sets the set's name and description", () => {
    const onChange = vi.fn();
    render(<LabelSetEditor value={BLANK} onChange={onChange} />);

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Toxicity" } });
    expect(onChange).toHaveBeenLastCalledWith({ ...BLANK, name: "Toxicity" });
  });
});
