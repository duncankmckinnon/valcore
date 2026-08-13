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
  // RefinePanel (rendered in the rail) reads useSetup, which calls this; a ready
  // gateway keeps this suite's existing behavior unchanged.
  setup: {
    get: vi.fn().mockResolvedValue({ keys: [] }),
  },
}));

const config: AppConfig = {
  models: ["gateway/anthropic:claude-sonnet-5", "gateway/openai:gpt-5"],
  // Deliberately not `models[0]`, so the defaulting assertions below would fail if the
  // editor went back to seeding new versions from the head of the catalog.
  default_model: "gateway/openai:gpt-5",
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
    expect((screen.getByLabelText("Model") as HTMLInputElement).value).toBe(config.default_model);
    expect(screen.getByRole("button", { name: "Create version" })).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
  });

  it("prefills every field from seedFrom, so a new version starts as an edit", async () => {
    const source = makeVersion({
      id: "v1",
      version_name: "tone-check",
      notes: "the prior notes",
      model: "gateway/openai:gpt-5",
      instructions: "Judge the tone.",
      prompt_template: "Rate {answer}",
      required_columns: ["answer"],
      tools: ["row_get"],
    });
    render(
      <VersionEditor version={null} seedFrom={source} evaluatorId="e1" config={config} />,
    );

    expect((screen.getByLabelText("Version name") as HTMLInputElement).value).toBe("tone-check");
    expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe(
      "Judge the tone.",
    );
    expect((screen.getByLabelText("Prompt template") as HTMLTextAreaElement).value).toBe(
      "Rate {answer}",
    );
    expect((screen.getByLabelText("Model") as HTMLInputElement).value).toBe(
      "gateway/openai:gpt-5",
    );

    // Seeded, not adopted: this is still a create, so it saves via createVersion.
    expect(screen.getByRole("button", { name: "Create version" })).not.toBeNull();
  });

  it("seeds a copy, so editing the draft cannot mutate the source version", async () => {
    const source = makeVersion({ required_columns: ["answer"], tools: ["row_get"] });
    const before = JSON.stringify(source);
    const user = userEvent.setup();
    render(
      <VersionEditor version={null} seedFrom={source} evaluatorId="e1" config={config} />,
    );

    await user.clear(screen.getByLabelText("Instructions"));
    await user.type(screen.getByLabelText("Instructions"), "Different.");

    expect(JSON.stringify(source)).toBe(before);
  });

  it("falls back to a blank form when there is no seed", () => {
    render(<VersionEditor version={null} evaluatorId="e1" config={config} />);

    expect((screen.getByLabelText("Version name") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe("");
  });

  it("accepts a model the catalog does not list", async () => {
    // The Gateway serves more models than the pinned pydantic-ai knows about, so the
    // field must take a well-formed name that is absent from the suggestions.
    const user = userEvent.setup();
    render(<VersionEditor version={null} evaluatorId="e1" config={config} />);

    const field = screen.getByLabelText("Model") as HTMLInputElement;
    const unlisted = "gateway/groq:llama-4-maverick";
    expect(config.models).not.toContain(unlisted);

    await user.clear(field);
    await user.type(field, unlisted);

    expect(field.value).toBe(unlisted);
  });

  it("offers the catalog as suggestions without constraining the field", () => {
    render(<VersionEditor version={null} evaluatorId="e1" config={config} />);

    const field = screen.getByLabelText("Model") as HTMLInputElement;
    expect(field.tagName).toBe("INPUT");
    expect(field.getAttribute("list")).toBeTruthy();

    const options = document.querySelectorAll(`#${field.getAttribute("list")} option`);
    expect([...options].map((o) => o.getAttribute("value"))).toEqual(config.models);
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

// The single collapsible disclosure ("Capabilities & tools") is the only button in the
// editor carrying aria-expanded, so it can be found without depending on its label text or
// on any styling class. Every other section is required to render with no disclosure at all.
function disclosureButton(): HTMLButtonElement {
  const expandable = screen
    .getAllByRole("button")
    .filter((button) => button.hasAttribute("aria-expanded"));
  expect(expandable.length).toBe(1);
  return expandable[0] as HTMLButtonElement;
}

// The info affordance beside a field label is the nearest ancestor button to that field's
// control. Climbing from the control avoids coupling to whether the field wraps in a
// <label> or a <div>, and to whatever accessible name the tooltip trigger carries.
function tooltipTriggerNear(control: HTMLElement): HTMLElement {
  let node = control.parentElement;
  while (node) {
    const buttons = within(node).queryAllByRole("button");
    if (buttons.length > 0) {
      return buttons[0];
    }
    node = node.parentElement;
  }
  throw new Error("no tooltip trigger found near the given control");
}

describe("VersionEditor: sectioned layout", () => {
  it("renders the four non-collapsible sections' fields without any interaction", () => {
    render(<VersionEditor version={makeVersion()} evaluatorId="e1" config={config} />);

    // Identity
    expect(screen.getByLabelText("Version name")).not.toBeNull();
    expect(screen.getByLabelText("Model")).not.toBeNull();
    // Judgment
    expect(screen.getByLabelText("Instructions")).not.toBeNull();
    expect(screen.getByLabelText("Prompt template")).not.toBeNull();
    // Inputs — the chips editor's add box
    expect(screen.getByLabelText("Add required column")).not.toBeNull();
    // Output contract
    expect(screen.getByLabelText("Score kind")).not.toBeNull();
    expect(screen.getByLabelText("Score field")).not.toBeNull();
    expect(screen.getByLabelText("Field 0 name")).not.toBeNull();
  });

  it("collapses Capabilities & tools by default, hiding its fields and reporting aria-expanded=false", () => {
    render(<VersionEditor version={makeVersion()} evaluatorId="e1" config={config} />);

    // config supplies FileSystem/Shell capabilities and a row_get tool; all live in the
    // one collapsible section, so none are in the DOM until it is opened.
    expect(screen.queryByRole("checkbox", { name: "FileSystem" })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: "Shell" })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: "row_get" })).toBeNull();

    expect(disclosureButton().getAttribute("aria-expanded")).toBe("false");
  });

  it("reveals the capability and tool fields and flips aria-expanded when the disclosure is clicked", async () => {
    const user = userEvent.setup();
    render(<VersionEditor version={makeVersion()} evaluatorId="e1" config={config} />);

    await user.click(disclosureButton());

    expect(disclosureButton().getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("checkbox", { name: "FileSystem" })).not.toBeNull();
    expect(screen.getByRole("checkbox", { name: "Shell" })).not.toBeNull();
    expect(screen.getByRole("checkbox", { name: "row_get" })).not.toBeNull();
  });

  it("shows the frozen banner and renders the visible fields read-only for a frozen version", () => {
    render(
      <VersionEditor version={makeVersion({ frozen: true })} evaluatorId="e1" config={config} />,
    );

    expect(screen.getByText("Frozen")).not.toBeNull();
    expect(screen.getByText(/read-only/i)).not.toBeNull();
    expect((screen.getByLabelText("Version name") as HTMLInputElement).readOnly).toBe(true);
    expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).readOnly).toBe(true);
    // `readOnly`, not `disabled`: the old control was a <select>, which has no readOnly
    // attribute. As an input it now matches its siblings and stays selectable, so a frozen
    // version's model string can still be copied.
    expect((screen.getByLabelText("Model") as HTMLInputElement).readOnly).toBe(true);
  });
});

describe("VersionEditor: form footer", () => {
  it("surfaces a blocker and disables Save when a field is invalid", async () => {
    const user = userEvent.setup();
    render(<VersionEditor version={makeVersion()} evaluatorId="e1" config={config} />);

    const save = screen.getByRole("button", { name: "Save changes" });
    expect((save as HTMLButtonElement).disabled).toBe(false);

    await user.clear(screen.getByLabelText("Version name"));

    // The footer explains the block via a role=status region (distinct from the inline
    // field errors, which keep role=alert), and Save is disabled.
    const blocker = screen.getByRole("status");
    expect(blocker.textContent?.trim().length ?? 0).toBeGreaterThan(0);
    expect((save as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows no blocker and enables Save with the dynamic label when the version is valid", () => {
    render(<VersionEditor version={makeVersion()} evaluatorId="e1" config={config} />);

    // Not saving and no errors, so the footer carries no status blocker.
    expect(screen.queryByRole("status")).toBeNull();
    const save = screen.getByRole("button", { name: "Save changes" });
    expect((save as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("VersionEditor: rail preview", () => {
  it("lists one line per output field derived from the field names", () => {
    render(<VersionEditor version={makeVersion()} evaluatorId="e1" config={config} />);

    // The preview renders a `"field": "…"` line per output field; the field names appear
    // as quoted keys, which the output-field editor (input values, not text) never emits.
    expect(screen.getByText(/"verdict":/)).not.toBeNull();
    expect(screen.getByText(/"confidence":/)).not.toBeNull();
    expect(screen.queryByText(/"missing_field":/)).toBeNull();
  });

  it("updates the preview when an output field is removed", async () => {
    const user = userEvent.setup();
    render(<VersionEditor version={makeVersion()} evaluatorId="e1" config={config} />);

    expect(screen.getByText(/"verdict":/)).not.toBeNull();

    // Removing `verdict` (field 0) leaves a numeric score on `confidence`, so the form stays
    // otherwise coherent while the preview drops the removed field's line.
    await user.click(screen.getByRole("button", { name: "Remove field 0" }));

    expect(screen.queryByText(/"verdict":/)).toBeNull();
    expect(screen.getByText(/"confidence":/)).not.toBeNull();
  });

  it("keeps the refine panel available in the rail", () => {
    render(<VersionEditor version={makeVersion()} evaluatorId="e1" config={config} />);

    expect(screen.getByLabelText("Refine instruction")).not.toBeNull();
  });
});

describe("VersionEditor: field tooltips", () => {
  it("opens the model tooltip and exposes its explanatory text", async () => {
    const user = userEvent.setup();
    render(<VersionEditor version={makeVersion()} evaluatorId="e1" config={config} />);

    const trigger = tooltipTriggerNear(screen.getByLabelText("Model"));
    expect(screen.queryByRole("tooltip")).toBeNull();

    await user.click(trigger);

    const popover = screen.getByRole("tooltip");
    expect(popover.textContent?.trim().length ?? 0).toBeGreaterThan(0);
  });

  it("explains in the prompt-template tooltip that braced names must match required columns", async () => {
    const user = userEvent.setup();
    render(<VersionEditor version={makeVersion()} evaluatorId="e1" config={config} />);

    const trigger = tooltipTriggerNear(screen.getByLabelText("Prompt template"));
    await user.click(trigger);

    // The requirement is explicit: the copy must tie braced names to the required columns.
    const popover = screen.getByRole("tooltip");
    expect(popover.textContent ?? "").toMatch(/required columns/i);
  });
});
