import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VersionEditor } from "./VersionEditor";
import type { AppConfig } from "./VersionEditor";
import { evaluators } from "../api/client";
import type { EvaluatorVersion, OutputField } from "../api/types";

vi.mock("../api/client", () => ({
  evaluators: {
    updateVersion: vi.fn(),
    copyVersion: vi.fn(),
    refine: vi.fn(),
  },
}));

const config: AppConfig = {
  models: ["gateway/anthropic:claude-sonnet-5", "gateway/openai:gpt-5"],
  tools: ["row_get"],
  capabilities: ["FileSystem", "Shell"],
};

const enumField: OutputField = {
  name: "verdict",
  type: "enum",
  description: "the verdict",
  required: true,
  enum_values: ["pass", "fail"],
  minimum: null,
  maximum: null,
};

const numericField: OutputField = {
  name: "confidence",
  type: "int",
  description: "confidence",
  required: true,
  enum_values: null,
  minimum: 0,
  maximum: 10,
};

function makeVersion(overrides: Partial<EvaluatorVersion> = {}): EvaluatorVersion {
  return {
    id: "v1",
    created_at: "2026-07-01T00:00:00Z",
    evaluator_id: "e1",
    version_name: "v1",
    notes: "",
    frozen: false,
    model: "gateway/anthropic:claude-sonnet-5",
    instructions: "Judge the answer.",
    prompt_template: "{answer}",
    required_columns: ["answer"],
    output_fields: [enumField, numericField],
    score_field: "confidence",
    score_kind: "numeric",
    score_labels: null,
    score_minimum: 0,
    score_maximum: 10,
    capabilities: [],
    tools: [],
    ...overrides,
  };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("VersionEditor", () => {
  it("renders a version's values into the form", () => {
    render(<VersionEditor version={makeVersion()} config={config} />);

    expect((screen.getByLabelText("Version name") as HTMLInputElement).value).toBe("v1");
    expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe(
      "Judge the answer.",
    );
    expect((screen.getByLabelText("Prompt template") as HTMLTextAreaElement).value).toBe(
      "{answer}",
    );
  });

  it("editing instructions and saving calls updateVersion with the new text", async () => {
    vi.mocked(evaluators.updateVersion).mockResolvedValue(makeVersion());
    const user = userEvent.setup();
    render(<VersionEditor version={makeVersion()} config={config} />);

    const instructions = screen.getByLabelText("Instructions");
    await user.clear(instructions);
    await user.type(instructions, "Be stricter.");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(evaluators.updateVersion).toHaveBeenCalledWith(
      "e1",
      "v1",
      expect.objectContaining({ instructions: "Be stricter." }),
    );
    expect(evaluators.copyVersion).not.toHaveBeenCalled();
  });

  it("renders a frozen version read-only and saves it as a new version", async () => {
    vi.mocked(evaluators.copyVersion).mockResolvedValue(
      makeVersion({ id: "v2", frozen: false }),
    );
    vi.mocked(evaluators.updateVersion).mockResolvedValue(makeVersion({ id: "v2" }));
    const user = userEvent.setup();
    render(<VersionEditor version={makeVersion({ frozen: true })} config={config} />);

    expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).readOnly).toBe(true);
    const save = screen.getByRole("button", { name: "Save as new version" });
    expect(save).not.toBeNull();

    await user.click(save);

    expect(evaluators.copyVersion).toHaveBeenCalledWith("e1", "v1");
    expect(evaluators.updateVersion).toHaveBeenCalledWith("e1", "v2", expect.any(Object));
  });

  it("restricts the score-field select to enum fields for a categorical score kind", async () => {
    const user = userEvent.setup();
    render(<VersionEditor version={makeVersion()} config={config} />);

    const scoreField = screen.getByLabelText("Score field") as HTMLSelectElement;
    expect(within(scoreField).queryByRole("option", { name: "confidence" })).not.toBeNull();

    await user.selectOptions(screen.getByLabelText("Score kind"), "categorical");

    const options = within(scoreField)
      .getAllByRole("option")
      .map((option) => (option as HTMLOptionElement).value);
    expect(options).toEqual(["verdict"]);
  });
});
