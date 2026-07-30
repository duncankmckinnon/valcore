import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ConfigDiff } from "./ConfigDiff";
import type { GeneratedConfig } from "../api/types";

const current: GeneratedConfig = {
  name: "Judge",
  version_name: "v1",
  instructions: "old instructions",
  prompt_template: "{answer}",
  required_columns: ["answer"],
  output_fields: [],
  score_field: "verdict",
  score_kind: "numeric",
  score_labels: null,
  score_minimum: 0,
  score_maximum: 1,
  capabilities: [],
  tools: [],
  rationale: "",
};

const proposed: GeneratedConfig = {
  ...current,
  instructions: "new instructions",
  score_kind: "categorical",
};

const changedFields = ["instructions", "score_kind"];

describe("ConfigDiff", () => {
  it("shows only the changed fields", () => {
    render(
      <ConfigDiff
        current={current}
        proposed={proposed}
        changedFields={changedFields}
        onApply={vi.fn()}
      />,
    );

    expect(screen.queryByText("instructions")).not.toBeNull();
    expect(screen.queryByText("score_kind")).not.toBeNull();
    expect(screen.queryByText("prompt_template")).toBeNull();
  });

  it("Accept applies one field's new value", async () => {
    const onApply = vi.fn();
    const user = userEvent.setup();
    render(
      <ConfigDiff
        current={current}
        proposed={proposed}
        changedFields={changedFields}
        onApply={onApply}
      />,
    );

    await user.click(screen.getByLabelText("Accept instructions"));

    expect(onApply).toHaveBeenCalledWith({ instructions: "new instructions" });
    expect(screen.queryByText("instructions")).toBeNull();
    expect(screen.queryByText("score_kind")).not.toBeNull();
  });

  it("Reject keeps the old value and drops the row", async () => {
    const onApply = vi.fn();
    const user = userEvent.setup();
    render(
      <ConfigDiff
        current={current}
        proposed={proposed}
        changedFields={changedFields}
        onApply={onApply}
      />,
    );

    await user.click(screen.getByLabelText("Reject instructions"));

    expect(onApply).not.toHaveBeenCalled();
    expect(screen.queryByText("instructions")).toBeNull();
    expect(screen.queryByText("score_kind")).not.toBeNull();
  });

  it("Accept all applies every change", async () => {
    const onApply = vi.fn();
    const user = userEvent.setup();
    render(
      <ConfigDiff
        current={current}
        proposed={proposed}
        changedFields={changedFields}
        onApply={onApply}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Accept all" }));

    expect(onApply).toHaveBeenCalledWith({
      instructions: "new instructions",
      score_kind: "categorical",
    });
  });
});
