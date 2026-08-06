import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LabelMixEditor } from "./LabelMixEditor";
import type { LabelMixPercents } from "./labelMix";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// A controlled harness so successive edits accumulate, mirroring how the generation forms
// wire the component up.
function renderStateful(
  props: { labels?: string[]; percents?: LabelMixPercents; enabled?: boolean; count?: number } = {},
  spies: { onChangePercents?: (percents: LabelMixPercents) => void } = {},
) {
  function Harness() {
    const [percents, setPercents] = useState<LabelMixPercents>(props.percents ?? {});
    const [enabled, setEnabled] = useState(props.enabled ?? false);
    return (
      <LabelMixEditor
        labels={props.labels ?? ["pass", "fail"]}
        percents={percents}
        enabled={enabled}
        count={props.count ?? 10}
        onChangeEnabled={setEnabled}
        onChangePercents={(next) => {
          spies.onChangePercents?.(next);
          setPercents(next);
        }}
      />
    );
  }
  return render(<Harness />);
}

describe("LabelMixEditor", () => {
  it("renders nothing when there are no labels to distribute over", () => {
    // A numeric score space or an unfinished label list has nothing to apportion.
    const { container } = renderStateful({ labels: [] });

    expect(container.innerHTML).toBe("");
  });

  it("hides the percent rows until the mix is switched on", () => {
    renderStateful({ enabled: false });

    expect(screen.queryByLabelText("Percent for pass")).toBeNull();
    expect(screen.getByText(/the description and instructions decide the mix/i)).toBeTruthy();
  });

  it("seeds an even split on first enable so it opens in a valid state", async () => {
    const user = userEvent.setup();
    renderStateful({ labels: ["pass", "fail", "borderline"], enabled: false });

    await user.click(screen.getByRole("checkbox", { name: /prescribe label distribution/i }));

    expect(screen.getByText("Total 100%")).toBeTruthy();
    expect((screen.getByLabelText("Percent for pass") as HTMLInputElement).value).toBe("34");
  });

  it("keeps percents already set when re-enabled", async () => {
    const user = userEvent.setup();
    renderStateful({ percents: { pass: 20, fail: 80 }, enabled: false });

    await user.click(screen.getByRole("checkbox", { name: /prescribe label distribution/i }));

    expect((screen.getByLabelText("Percent for pass") as HTMLInputElement).value).toBe("20");
  });

  it("shows the apportioned row count beside each label", () => {
    renderStateful({ percents: { pass: 30, fail: 70 }, enabled: true, count: 10 });

    expect(screen.getByText("3 rows")).toBeTruthy();
    expect(screen.getByText("7 rows")).toBeTruthy();
  });

  it("recomputes row counts when the percents are edited", async () => {
    const user = userEvent.setup();
    renderStateful({ percents: { pass: 50, fail: 50 }, enabled: true, count: 10 });

    const pass = screen.getByLabelText("Percent for pass");
    await user.clear(pass);
    await user.type(pass, "20");
    const fail = screen.getByLabelText("Percent for fail");
    await user.clear(fail);
    await user.type(fail, "80");

    expect(screen.getByText("2 rows")).toBeTruthy();
    expect(screen.getByText("8 rows")).toBeTruthy();
  });

  it("withholds the row-count preview while the total is invalid", async () => {
    // A normalised preview beside a blocking error would read as a workable plan.
    const user = userEvent.setup();
    renderStateful({ percents: { pass: 50, fail: 50 }, enabled: true, count: 10 });

    const input = screen.getByLabelText("Percent for pass");
    await user.clear(input);
    await user.type(input, "20");

    expect(screen.queryByText(/rows$/)).toBeNull();
    expect(screen.getByText(/must add up to 100%/i)).toBeTruthy();
  });

  it("warns when the percents do not add up to 100", async () => {
    const user = userEvent.setup();
    renderStateful({ percents: { pass: 50, fail: 50 }, enabled: true });

    const input = screen.getByLabelText("Percent for pass");
    await user.clear(input);
    await user.type(input, "20");

    expect(screen.getByText(/must add up to 100%/i)).toBeTruthy();
  });

  it("clears the warning once the total reaches 100", () => {
    renderStateful({ percents: { pass: 40, fail: 60 }, enabled: true });

    expect(screen.queryByText(/must add up to 100%/i)).toBeNull();
    expect(screen.getByText("Total 100%")).toBeTruthy();
  });

  it("resets to an even split on demand", async () => {
    const user = userEvent.setup();
    const onChangePercents = vi.fn();
    renderStateful({ percents: { pass: 90, fail: 10 }, enabled: true }, { onChangePercents });

    await user.click(screen.getByRole("button", { name: /even split/i }));

    expect(onChangePercents).toHaveBeenCalledWith({ pass: 50, fail: 50 });
  });

  it("treats a cleared input as zero rather than NaN", async () => {
    const user = userEvent.setup();
    renderStateful({ percents: { pass: 50, fail: 50 }, enabled: true });

    await user.clear(screen.getByLabelText("Percent for pass"));

    expect(screen.getByText("Total 50%")).toBeTruthy();
  });

  it("refuses a negative percent", async () => {
    const user = userEvent.setup();
    renderStateful({ percents: { pass: 50, fail: 50 }, enabled: true });

    const input = screen.getByLabelText("Percent for pass");
    await user.clear(input);
    await user.type(input, "-5");

    expect((input as HTMLInputElement).value).toBe("5");
  });
});
