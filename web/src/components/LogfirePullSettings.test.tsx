import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { LogfirePullSettings } from "./LogfirePullSettings";
import type { DatasetLogfirePull } from "../api/types";

afterEach(cleanup);

function madePull(overrides: Partial<DatasetLogfirePull> = {}): DatasetLogfirePull {
  return {
    sql: "SELECT span_id FROM records",
    sample_n: 12,
    seed: 7,
    min_timestamp: null,
    max_timestamp: null,
    label_column: null,
    ...overrides,
  };
}

describe("LogfirePullSettings", () => {
  it("renders nothing when the dataset was not pulled", () => {
    const { container } = render(<LogfirePullSettings pull={null} />);
    expect(container.innerHTML).toBe("");
  });

  it("shows the SQL, sample size, and seed", () => {
    render(<LogfirePullSettings pull={madePull()} />);
    expect(screen.getByText("SELECT span_id FROM records")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
  });
});
