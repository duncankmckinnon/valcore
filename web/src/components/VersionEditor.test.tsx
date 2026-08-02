import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VersionEditor } from "./VersionEditor";
import type { AppConfig } from "./VersionEditor";
import { evaluators } from "../api/client";
import type { EvaluatorVersion, OutputField } from "../api/types";

vi.mock("../api/client", () => ({
  evaluators: {
    createVersion: vi.fn(),
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

describe("VersionEditor: existing version mode", () => {
  it("renders a version's values into the form", () => {
    render(<VersionEditor version={makeVersion()} evaluatorId="e1" config={config} />);

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
    render(<VersionEditor version={makeVersion()} evaluatorId="e1" config={config} />);

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
    expect(evaluators.createVersion).not.toHaveBeenCalled();
  });

  it("renders a frozen version read-only and saves it as a new version", async () => {
    vi.mocked(evaluators.copyVersion).mockResolvedValue(makeVersion({ id: "v2", frozen: false }));
    vi.mocked(evaluators.updateVersion).mockResolvedValue(makeVersion({ id: "v2" }));
    const user = userEvent.setup();
    render(<VersionEditor version={makeVersion({ frozen: true })} evaluatorId="e1" config={config} />);

    expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).readOnly).toBe(true);
    const save = screen.getByRole("button", { name: "Save as new version" });
    expect(save).not.toBeNull();

    await user.click(save);

    expect(evaluators.copyVersion).toHaveBeenCalledWith("e1", "v1");
    expect(evaluators.updateVersion).toHaveBeenCalledWith("e1", "v2", expect.any(Object));
    expect(evaluators.createVersion).not.toHaveBeenCalled();
  });

  it("restricts the score-field select to enum fields for a categorical score kind", async () => {
    const user = userEvent.setup();
    render(<VersionEditor version={makeVersion()} evaluatorId="e1" config={config} />);

    const scoreField = screen.getByLabelText("Score field") as HTMLSelectElement;
    expect(within(scoreField).queryByRole("option", { name: "confidence" })).not.toBeNull();

    await user.selectOptions(screen.getByLabelText("Score kind"), "categorical");

    const options = within(scoreField)
      .getAllByRole("option")
      .map((option) => (option as HTMLOptionElement).value);
    expect(options).toEqual(["verdict"]);
  });
});

describe("VersionEditor: inline validation", () => {
  it("disables Save and shows an inline error when an existing version is edited into an invalid state", async () => {
    const user = userEvent.setup();
    render(<VersionEditor version={makeVersion()} evaluatorId="e1" config={config} />);

    // A valid version starts with Save enabled.
    const save = screen.getByRole("button", { name: "Save changes" });
    expect((save as HTMLButtonElement).disabled).toBe(false);

    await user.clear(screen.getByLabelText("Version name"));

    expect((save as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
  });

  it("keeps score_labels in sync when a categorical score field's enum values change", async () => {
    const user = userEvent.setup();
    const categoricalVersion = makeVersion({
      score_kind: "categorical",
      score_field: "verdict",
      score_labels: ["pass", "fail"],
      score_minimum: null,
      score_maximum: null,
    });
    render(<VersionEditor version={categoricalVersion} evaluatorId="e1" config={config} />);

    // `verdict` is the first output field, so its enum-values input is "Field 0 enum values".
    const enumValues = screen.getByLabelText("Field 0 enum values");
    await user.clear(enumValues);
    await user.type(enumValues, "pass, fail, skip");

    // If score_labels tracked the enum change, validation raises no score_labels error and
    // Save stays enabled on a config the user could not otherwise fix from this form.
    expect(screen.queryByText(/score_labels/i)).toBeNull();
    expect((screen.getByRole("button", { name: "Save changes" }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });
});

describe("VersionEditor: draft mode", () => {
  it("renders empty fields, defaults the model, and shows a Create version button", () => {
    render(<VersionEditor version={null} evaluatorId="e1" config={config} />);

    expect((screen.getByLabelText("Version name") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe("");
    expect((screen.getByLabelText("Prompt template") as HTMLTextAreaElement).value).toBe("");
    expect((screen.getByLabelText("Model") as HTMLSelectElement).value).toBe(config.models[0]);
    expect(screen.getByRole("button", { name: "Create version" })).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
  });

  it("disables Save and shows an inline error for an incomplete draft", () => {
    render(<VersionEditor version={null} evaluatorId="e1" config={config} />);

    const save = screen.getByRole("button", { name: "Create version" });
    expect((save as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
  });

  it("enables Save once the draft is valid and creates a version with the evaluator id", async () => {
    vi.mocked(evaluators.createVersion).mockResolvedValue(makeVersion());
    const user = userEvent.setup();
    render(<VersionEditor version={null} evaluatorId="e1" config={config} />);

    await user.type(screen.getByLabelText("Version name"), "v1");

    await user.type(screen.getByLabelText("Add required column"), "answer");
    await user.click(screen.getByRole("button", { name: "Add" }));

    await user.click(screen.getByRole("button", { name: "Add field" }));
    await user.type(screen.getByLabelText("Field 0 name"), "confidence");
    await user.selectOptions(screen.getByLabelText("Field 0 type"), "int");

    // A numeric score kind points score_field at the only compatible (int) field.
    await user.selectOptions(screen.getByLabelText("Score kind"), "numeric");

    const save = screen.getByRole("button", { name: "Create version" });
    expect((save as HTMLButtonElement).disabled).toBe(false);

    await user.click(save);

    expect(evaluators.createVersion).toHaveBeenCalledWith(
      "e1",
      expect.objectContaining({ version_name: "v1", score_field: "confidence" }),
    );
    expect(evaluators.updateVersion).not.toHaveBeenCalled();
    expect(evaluators.copyVersion).not.toHaveBeenCalled();
  });
});
