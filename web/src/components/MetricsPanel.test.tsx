import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import MetricsPanel from "./MetricsPanel";

const CATEGORICAL = {
  n: 4,
  accuracy: 0.75,
  per_label: {
    good: { precision: 1.0, recall: 0.5, f1: 0.667, support: 2 },
    bad: { precision: 0.667, recall: 1.0, f1: 0.8, support: 2 },
  },
  confusion: {
    good: { good: 1, bad: 1 },
    bad: { good: 0, bad: 2 },
  },
  cohens_kappa: 0.5,
};

const NUMERIC = { n: 5, mae: 0.4, rmse: 0.6, pearson: 0.9, spearman: 0.85 };

const NUMERIC_NO_VARIANCE = { n: 3, mae: 0.0, rmse: 0.0, pearson: null, spearman: null };

function confusionCell(actual: string, predicted: string): string {
  const cell = document.querySelector(
    `[data-actual="${actual}"][data-predicted="${predicted}"]`,
  );
  return cell?.textContent ?? "";
}

afterEach(() => {
  cleanup();
});

describe("MetricsPanel", () => {
  it("renders categorical metrics with per-label rows and confusion cells", () => {
    render(<MetricsPanel metrics={CATEGORICAL} />);

    // Accuracy and Cohen's κ live in the summary; scope the κ lookup there since the
    // value 0.500 also appears as a per-label recall cell.
    const summary = document.querySelector(".metrics-summary") as HTMLElement;
    expect(within(summary).getByText("75.0%")).toBeTruthy();
    expect(within(summary).getByText("0.500")).toBeTruthy();

    // Per-label table has a row per label.
    const perLabel = document.querySelector(".metrics-per-label")!;
    expect(within(perLabel as HTMLElement).getByText("good")).toBeTruthy();
    expect(within(perLabel as HTMLElement).getByText("bad")).toBeTruthy();

    // Confusion matrix cells carry the right counts.
    expect(confusionCell("good", "good")).toBe("1");
    expect(confusionCell("good", "bad")).toBe("1");
    expect(confusionCell("bad", "good")).toBe("0");
    expect(confusionCell("bad", "bad")).toBe("2");
  });

  it("renders numeric metrics", () => {
    render(<MetricsPanel metrics={NUMERIC} />);

    expect(screen.getByText("MAE")).toBeTruthy();
    expect(screen.getByText("0.400")).toBeTruthy();
    expect(screen.getByText("0.600")).toBeTruthy();
    expect(screen.getByText("0.900")).toBeTruthy();
    expect(screen.getByText("0.850")).toBeTruthy();
  });

  it("shows n/a for null correlations with an explanation", () => {
    render(<MetricsPanel metrics={NUMERIC_NO_VARIANCE} />);

    expect(screen.getAllByText("n/a").length).toBe(2);
    expect(screen.getByText(/zero variance/i)).toBeTruthy();
  });
});
