// Tests for the top-up modal. Coverage focuses on the prefill (stored settings become the
// starting point) and on shape being fixed by the dataset rather than the form.

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import GenerateMoreRows from "./GenerateMoreRows";
import { datasets } from "../api/client";
import type { Dataset, DatasetGeneration, DatasetRow } from "../api/types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    datasets: { ...actual.datasets, generateRows: vi.fn() },
  };
});

const generateRowsMock = vi.mocked(datasets.generateRows);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function madeDataset(overrides: Partial<Dataset> = {}): Dataset {
  return {
    id: "d1",
    created_at: "2026-08-05T00:00:00Z",
    name: "Support QA",
    description: "support questions",
    columns: ["question", "answer"],
    label_schema: { kind: "categorical", labels: ["pass", "fail"], minimum: null, maximum: null },
    ...overrides,
  };
}

function madeGeneration(overrides: Partial<DatasetGeneration> = {}): DatasetGeneration {
  return {
    count: 20,
    instructions: null,
    column_notes: null,
    label_mix: null,
    label_guidance: null,
    include_labels: true,
    source_version_id: null,
    ...overrides,
  };
}

function madeRows(n: number): DatasetRow[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `r${i}`,
    created_at: "2026-08-05T00:00:00Z",
    dataset_id: "d1",
    idx: i,
    data: { question: "q", answer: "a" },
    label: null,
    suggested_label: null,
    label_reasoning: null,
    label_source: null,
    note: null,
  }));
}

function renderModal(
  overrides: Partial<React.ComponentProps<typeof GenerateMoreRows>> = {},
) {
  const props = {
    open: true,
    dataset: madeDataset(),
    generation: madeGeneration(),
    maxCount: 200,
    onGenerated: vi.fn(),
    onClose: vi.fn(),
    ...overrides,
  };
  render(<GenerateMoreRows {...props} />);
  return props;
}

describe("GenerateMoreRows", () => {
  it("prefills every steer from the stored settings", () => {
    renderModal({
      generation: madeGeneration({
        count: 7,
        instructions: "be subtle",
        column_notes: { question: "a support ticket" },
        label_mix: { pass: 0.25, fail: 0.75 },
      }),
    });

    expect(screen.getByLabelText("Rows to add")).toHaveValue(7);
    expect(screen.getByLabelText("Instructions")).toHaveValue("be subtle");
    expect(screen.getByLabelText("Note for question")).toHaveValue("a support ticket");
    // Stored proportions come back as the whole percents the editor works in.
    expect(screen.getByLabelText("Percent for pass")).toHaveValue(25);
    expect(screen.getByLabelText("Percent for fail")).toHaveValue(75);
  });

  it("enables the mix editor only when a mix was stored", () => {
    renderModal({ generation: madeGeneration({ label_mix: { pass: 0.5, fail: 0.5 } }) });

    expect(screen.getByRole("checkbox", { name: /prescribe label distribution/i })).toBeChecked();
  });

  it("leaves the mix off when none was stored", () => {
    renderModal({ generation: madeGeneration() });

    expect(
      screen.getByRole("checkbox", { name: /prescribe label distribution/i }),
    ).not.toBeChecked();
  });

  it("falls back to defaults for a dataset that was never generated", () => {
    // An uploaded dataset has no settings; the form still works, just unseeded.
    renderModal({ generation: null });

    expect(screen.getByLabelText("Rows to add")).toHaveValue(20);
    expect(screen.getByLabelText("Instructions")).toHaveValue("");
  });

  it("sends only count when nothing was stored or typed", async () => {
    const user = userEvent.setup();
    generateRowsMock.mockResolvedValue(madeRows(20));
    renderModal({ generation: null });

    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(generateRowsMock).toHaveBeenCalled());
    expect(generateRowsMock.mock.calls[0][1]).toEqual({ count: 20 });
  });

  it("sends the prefilled steers back on submit", async () => {
    const user = userEvent.setup();
    generateRowsMock.mockResolvedValue(madeRows(5));
    renderModal({
      generation: madeGeneration({
        count: 5,
        instructions: "be subtle",
        column_notes: { question: "a support ticket" },
        label_mix: { pass: 0.5, fail: 0.5 },
      }),
    });

    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(generateRowsMock).toHaveBeenCalled());
    expect(generateRowsMock.mock.calls[0]).toEqual([
      "d1",
      {
        count: 5,
        instructions: "be subtle",
        column_notes: { question: "a support ticket" },
        label_mix: { pass: 0.5, fail: 0.5 },
      },
    ]);
  });

  it("reports how many rows were added", async () => {
    const user = userEvent.setup();
    generateRowsMock.mockResolvedValue(madeRows(3));
    const props = renderModal({ generation: madeGeneration({ count: 3 }) });

    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(props.onGenerated).toHaveBeenCalledWith(3));
  });

  it("offers notes for the dataset's own columns and no way to add more", () => {
    // Shape is fixed: new rows must match the columns already there.
    renderModal();

    expect(screen.getByLabelText("Note for question")).toBeInTheDocument();
    expect(screen.getByLabelText("Note for answer")).toBeInTheDocument();
    expect(screen.queryByLabelText("New column name")).toBeNull();
  });

  it("blocks Generate above the server's cap", async () => {
    const user = userEvent.setup();
    renderModal({ maxCount: 50 });

    const count = screen.getByLabelText("Rows to add");
    await user.clear(count);
    await user.type(count, "51");

    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
    expect(screen.getByText(/must be 50 or fewer/i)).toBeInTheDocument();
  });

  it("blocks Generate while a prescribed mix does not total 100", async () => {
    const user = userEvent.setup();
    renderModal({ generation: madeGeneration({ label_mix: { pass: 0.5, fail: 0.5 } }) });

    const pass = screen.getByLabelText("Percent for pass");
    await user.clear(pass);
    await user.type(pass, "10");

    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
  });

  it("offers no mix editor when the dataset carries no label space", () => {
    // An empty schema is the legal "no ground truth" state — nothing to distribute over.
    renderModal({ dataset: madeDataset({ label_schema: {} }) });

    expect(screen.queryByRole("checkbox", { name: /prescribe label distribution/i })).toBeNull();
  });

  it("prunes a stored note whose column has since been removed", async () => {
    const user = userEvent.setup();
    generateRowsMock.mockResolvedValue(madeRows(1));
    // 'context' was in the settings but an edit has since dropped it from the dataset;
    // sending it would fail the server's unknown-column check.
    renderModal({
      dataset: madeDataset({ columns: ["question"] }),
      generation: madeGeneration({
        count: 1,
        column_notes: { question: "a ticket", context: "gone" },
      }),
    });

    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(generateRowsMock).toHaveBeenCalled());
    expect(generateRowsMock.mock.calls[0][1].column_notes).toEqual({ question: "a ticket" });
  });

  it("keeps the form filled in when generation fails", async () => {
    const user = userEvent.setup();
    generateRowsMock.mockRejectedValue(new Error("boom"));
    const props = renderModal({ generation: madeGeneration({ instructions: "be subtle" }) });

    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(screen.getByLabelText("Instructions")).toHaveValue("be subtle"));
    expect(props.onGenerated).not.toHaveBeenCalled();
  });
});

// -- Redesigned modal chrome -------------------------------------------------
// The redesign adds a description, moves the actions into the footer, and routes the
// submit gate through FormFooter so a blocked submit says *why* it is blocked instead of
// staying silently disabled.

describe("GenerateMoreRows chrome", () => {
  it("describes that new rows append and reuse the stored settings", () => {
    renderModal();

    expect(screen.getByText(/append|stored settings|reuse/i)).toBeInTheDocument();
  });

  it("keeps the Cancel action wired to onClose from the footer", async () => {
    const user = userEvent.setup();
    const props = renderModal();

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(props.onClose).toHaveBeenCalled();
  });

  it("shows the blocking reason in the footer and disables Generate", async () => {
    const user = userEvent.setup();
    renderModal({ maxCount: 50 });

    const count = screen.getByLabelText("Rows to add");
    await user.clear(count);
    await user.type(count, "51");

    // FormFooter renders the first blocker as a status region rather than leaving the
    // button silently disabled.
    const blocker = screen.getByRole("status");
    expect(blocker.textContent).toMatch(/50/);
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
  });

  it("shows no blocker and enables Generate on a valid state", () => {
    // The ready path of FormFooter: a satisfiable form carries no status region and the
    // primary action is live.
    renderModal();

    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByRole("button", { name: "Generate" })).not.toBeDisabled();
  });

  it("surfaces the empty-count blocker and disables Generate at zero rows", async () => {
    // Emptying the number field drives count below one, which is the first blocker in the
    // top-to-bottom order.
    const user = userEvent.setup();
    renderModal();

    const count = screen.getByLabelText("Rows to add");
    await user.clear(count);

    expect(screen.getByRole("status").textContent).toMatch(/at least one row/i);
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
  });
});
