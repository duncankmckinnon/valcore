import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { GenerationSettings } from "./GenerationSettings";
import type { DatasetGeneration } from "../api/types";

afterEach(cleanup);

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

describe("GenerationSettings", () => {
  it("renders nothing for a dataset that was never generated", () => {
    // Uploaded and blank datasets have no settings; absence is normal, not an error.
    const { container } = render(<GenerationSettings generation={null} />);

    expect(container.innerHTML).toBe("");
  });

  it("always reports the row count that was asked for", () => {
    render(<GenerationSettings generation={madeGeneration({ count: 42 })} />);

    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("shows instructions and label guidance when present", () => {
    render(
      <GenerationSettings
        generation={madeGeneration({
          instructions: "be subtle",
          label_guidance: "partial is fail",
        })}
      />,
    );

    expect(screen.getByText("be subtle")).toBeInTheDocument();
    expect(screen.getByText("partial is fail")).toBeInTheDocument();
  });

  it("omits the free-text sections when they were never set", () => {
    render(<GenerationSettings generation={madeGeneration()} />);

    expect(screen.queryByText("Instructions")).toBeNull();
    expect(screen.queryByText("Label guidance")).toBeNull();
    expect(screen.queryByText("Column notes")).toBeNull();
  });

  it("lists each column note against its column", () => {
    render(
      <GenerationSettings
        generation={madeGeneration({ column_notes: { question: "a support ticket" } })}
      />,
    );

    expect(screen.getByText("question")).toBeInTheDocument();
    expect(screen.getByText(/a support ticket/)).toBeInTheDocument();
  });

  it("renders the mix back as percentages", () => {
    // Stored as proportions; a reader thinks in percents.
    render(
      <GenerationSettings generation={madeGeneration({ label_mix: { pass: 0.25, fail: 0.75 } })} />,
    );

    expect(screen.getByText("pass: 25%")).toBeInTheDocument();
    expect(screen.getByText("fail: 75%")).toBeInTheDocument();
  });

  it("keeps one decimal for a share that is not a whole percent", () => {
    render(<GenerationSettings generation={madeGeneration({ label_mix: { pass: 1 / 3 } })} />);

    expect(screen.getByText("pass: 33.3%")).toBeInTheDocument();
  });

  it("shows the source version as an id rather than a link", () => {
    // Provenance only: the version may since have changed or been deleted.
    render(<GenerationSettings generation={madeGeneration({ source_version_id: "v-abc" })} />);

    expect(screen.getByText("v-abc")).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });
});
