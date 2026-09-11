// Seeded generation, dataset -> evaluator direction. This modal derives an evaluator's
// column set from an existing dataset (its columns are the fixed, required set) while the
// user supplies criteria and per-column notes to steer the generated judge. The result is
// an editable draft handed to the version editor; the modal itself persists nothing.
//
// The label space now comes from the dataset's label sets rather than a single embedded
// schema: zero (the evaluator defines its own), one (used automatically), or more than one
// (the user must pick, defaulting to the first) — mirroring `_resolve_seed` in
// routes/evaluators.py. These tests mock only the client module (as the neighbouring
// component tests do) and exercise the real ColumnNotesEditor/LabelSchemaEditor so the
// locked-column and override behaviour is verified end to end rather than stubbed.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import EvaluatorFromDataset from "./EvaluatorFromDataset";
import { ApiError, evaluators, labelSets } from "../api/client";
import { GATEWAY_BLOCKER, useSetup } from "./useSetup";
import type { UseSetupResult } from "./useSetup";
import type { Dataset, GeneratedConfig, LabelSetProgress } from "../api/types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    // `create` and `createVersion` are stubbed only so a stray persistence call would be
    // observable: the modal must hand back a draft, never save one.
    evaluators: {
      ...actual.evaluators,
      generate: vi.fn(),
      create: vi.fn(),
      createVersion: vi.fn(),
    },
    labelSets: { ...actual.labelSets, list: vi.fn() },
  };
});

// Generation needs the gateway key as much as a run does; the hook is mocked directly
// (rather than driving it through `../api/client`'s `setup.get`) so each test can set
// `gatewayReady` without re-exercising useSetup's own fetch/loading machinery, which has
// its own dedicated suite in useSetup.test.tsx.
vi.mock("./useSetup", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./useSetup")>();
  return { ...actual, useSetup: vi.fn() };
});

const generateMock = vi.mocked(evaluators.generate);
const createMock = vi.mocked(evaluators.create);
const createVersionMock = vi.mocked(evaluators.createVersion);
const listLabelSetsMock = vi.mocked(labelSets.list);
const useSetupMock = vi.mocked(useSetup);

function makeSetupResult(overrides: Partial<UseSetupResult> = {}): UseSetupResult {
  return {
    status: null,
    gatewayReady: true,
    loading: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  };
}

function makeLabelSet(overrides: Partial<LabelSetProgress> = {}): LabelSetProgress {
  return {
    id: "ls1",
    created_at: "2026-01-01T00:00:00Z",
    dataset_id: "d1",
    name: "quality",
    description: "",
    kind: "categorical",
    labels: [{ name: "good", description: "" }, { name: "bad", description: "" }],
    minimum: null,
    maximum: null,
    annotated_count: 0,
    row_count: 0,
    ...overrides,
  };
}

function madeDataset(overrides: Partial<Dataset> = {}): Dataset {
  return {
    id: "d1",
    created_at: "2026-01-01T00:00:00Z",
    name: "Support tickets",
    description: "desc",
    columns: ["question", "answer"],
    ...overrides,
  };
}

function madeDraft(): GeneratedConfig {
  return {
    name: "Answer quality",
    version_name: "v1",
    instructions: "Judge whether the answer resolves the question.",
    prompt_template: "Q: {question}\nA: {answer}",
    required_columns: ["question", "answer"],
    output_fields: [],
    score_field: "score",
    score_kind: "categorical",
    score_labels: ["good", "bad"],
    score_minimum: null,
    score_maximum: null,
    capabilities: [],
    tools: [],
    rationale: "derived from dataset d1",
  };
}

async function renderReady(props: {
  dataset?: Dataset;
  onGenerated?: (draft: GeneratedConfig) => void;
  onClose?: () => void;
} = {}) {
  const onGenerated = props.onGenerated ?? vi.fn();
  const onClose = props.onClose ?? vi.fn();
  render(
    <EvaluatorFromDataset
      open
      dataset={props.dataset ?? madeDataset()}
      onGenerated={onGenerated}
      onClose={onClose}
    />,
  );
  await waitFor(() => expect(listLabelSetsMock).toHaveBeenCalled());
  return { onGenerated, onClose };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

beforeEach(() => {
  useSetupMock.mockReturnValue(makeSetupResult());
  // Most tests exercise the single-label-set (auto-used) path by default.
  listLabelSetsMock.mockResolvedValue([makeLabelSet()]);
});

describe("EvaluatorFromDataset", () => {
  it("renders the dataset's columns locked with no control for adding columns", async () => {
    await renderReady({ dataset: madeDataset({ columns: ["question", "answer"] }) });

    expect(screen.getByLabelText("Note for question")).toBeInTheDocument();
    expect(screen.getByLabelText("Note for answer")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove question" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove answer" })).toBeNull();
    expect(screen.queryByLabelText("New column name")).toBeNull();
    expect(screen.queryByRole("button", { name: "Add column" })).toBeNull();
    expect(
      (screen.getByLabelText("Note for question") as HTMLInputElement).placeholder,
    ).toMatch(/assess/i);
  });

  it("sends dataset_id, every column, and column_notes on submit", async () => {
    const draft = madeDraft();
    generateMock.mockResolvedValue(draft);
    const user = userEvent.setup();
    await renderReady({ dataset: madeDataset({ columns: ["question", "answer"] }) });

    await user.type(screen.getByLabelText("Criteria"), "Does the answer resolve the ticket?");
    await user.type(screen.getByLabelText("Note for question"), "the customer's problem");

    await user.click(screen.getByRole("button", { name: "Generate evaluator" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalledTimes(1));
    const arg = generateMock.mock.calls[0][0];
    expect(arg.dataset_id).toBe("d1");
    expect(arg.column_notes).toEqual({ question: "the customer's problem" });
    expect(arg.criteria).toBe("Does the answer resolve the ticket?");
    expect(arg.columns).toEqual(["question", "answer"]);
  });

  it("narrows columns to the included ones and drops an excluded column's note", async () => {
    generateMock.mockResolvedValue(madeDraft());
    const user = userEvent.setup();
    await renderReady({ dataset: madeDataset({ columns: ["question", "answer"] }) });

    await user.type(screen.getByLabelText("Criteria"), "grade it");
    await user.type(screen.getByLabelText("Note for answer"), "stale note");
    await user.click(screen.getByLabelText("Include answer"));

    await user.click(screen.getByRole("button", { name: "Generate evaluator" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalledTimes(1));
    const arg = generateMock.mock.calls[0][0];
    expect(arg.columns).toEqual(["question"]);
    expect(arg.column_notes).toEqual({});
  });

  it("sends label_set_id automatically when the dataset has exactly one label set", async () => {
    generateMock.mockResolvedValue(madeDraft());
    const user = userEvent.setup();
    await renderReady();

    expect(screen.getByLabelText("Use one of this dataset's label sets")).toBeChecked();
    expect(screen.queryByLabelText("Label set")).toBeNull(); // no picker needed for one set

    await user.type(screen.getByLabelText("Criteria"), "grade it");
    await user.click(screen.getByRole("button", { name: "Generate evaluator" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalledTimes(1));
    expect(generateMock.mock.calls[0][0].label_set_id).toBe("ls1");
    expect(generateMock.mock.calls[0][0]).not.toHaveProperty("label_schema");
  });

  it("requires a picker and defaults to the first label set when more than one exists", async () => {
    listLabelSetsMock.mockResolvedValue([
      makeLabelSet({ id: "ls1", name: "quality" }),
      makeLabelSet({ id: "ls2", name: "toxicity" }),
    ]);
    generateMock.mockResolvedValue(madeDraft());
    const user = userEvent.setup();
    await renderReady();

    const picker = await screen.findByLabelText("Label set");
    expect((picker as HTMLSelectElement).value).toBe("ls1");

    await user.selectOptions(picker, "ls2");
    await user.type(screen.getByLabelText("Criteria"), "grade it");
    await user.click(screen.getByRole("button", { name: "Generate evaluator" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalledTimes(1));
    expect(generateMock.mock.calls[0][0].label_set_id).toBe("ls2");
  });

  it("sends a prescribed label_schema once the dataset's label sets are turned off", async () => {
    generateMock.mockResolvedValue(madeDraft());
    const user = userEvent.setup();
    await renderReady();

    await user.click(screen.getByLabelText("Use one of this dataset's label sets"));
    expect(screen.getByPlaceholderText("Add a label")).toBeInTheDocument();
    expect(screen.getByText(/cannot be validated against it/)).toBeInTheDocument();

    await user.type(screen.getByLabelText("Criteria"), "grade it");
    await user.click(screen.getByRole("button", { name: "Generate evaluator" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalledTimes(1));
    const arg = generateMock.mock.calls[0][0];
    expect(arg).not.toHaveProperty("label_set_id");
    expect(arg.label_schema).toEqual({ kind: "categorical", labels: [], minimum: null, maximum: null });
  });

  it("blocks submission when every column is excluded", async () => {
    const user = userEvent.setup();
    await renderReady({ dataset: madeDataset({ columns: ["question"] }) });

    await user.type(screen.getByLabelText("Criteria"), "grade it");
    await user.click(screen.getByLabelText("Include question"));

    expect(screen.getByText("Include at least one column.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate evaluator" })).toBeDisabled();
  });

  it("explains the evaluator defines its own space when the dataset has no label sets", async () => {
    listLabelSetsMock.mockResolvedValue([]);
    await renderReady();

    expect(await screen.findByText(/no label sets/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("Use one of this dataset's label sets")).toBeNull();
  });

  it("hands the generated draft to the version editor without saving anything", async () => {
    const draft = madeDraft();
    generateMock.mockResolvedValue(draft);
    const user = userEvent.setup();
    const { onGenerated } = await renderReady();

    await user.type(screen.getByLabelText("Criteria"), "Judge answer quality.");
    await user.click(screen.getByRole("button", { name: "Generate evaluator" }));

    await waitFor(() => expect(onGenerated).toHaveBeenCalledWith(draft));
    expect(createMock).not.toHaveBeenCalled();
    expect(createVersionMock).not.toHaveBeenCalled();
  });

  it("surfaces a server error inside the modal and does not hand back a draft", async () => {
    generateMock.mockRejectedValue(
      new ApiError("columns and dataset_id are mutually exclusive", "ContractError", 400),
    );
    const user = userEvent.setup();
    const { onGenerated } = await renderReady();

    await user.type(screen.getByLabelText("Criteria"), "Judge answer quality.");
    await user.click(screen.getByRole("button", { name: "Generate evaluator" }));

    expect(
      await screen.findByText(/columns and dataset_id are mutually exclusive/i),
    ).toBeInTheDocument();
    expect(onGenerated).not.toHaveBeenCalled();
  });

  it("disables submit while the generate request is in flight", async () => {
    let resolve!: (draft: GeneratedConfig) => void;
    generateMock.mockReturnValue(
      new Promise<GeneratedConfig>((r) => {
        resolve = r;
      }),
    );
    const user = userEvent.setup();
    await renderReady();

    await user.type(screen.getByLabelText("Criteria"), "Judge answer quality.");
    const submit = screen.getByRole("button", { name: "Generate evaluator" });
    await user.click(submit);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Generate evaluator" })).toBeDisabled(),
    );
    expect(generateMock).toHaveBeenCalledTimes(1);

    resolve(madeDraft());
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Generate evaluator" })).not.toBeDisabled(),
    );
  });

  it("keeps submit disabled until criteria are entered", async () => {
    const user = userEvent.setup();
    await renderReady();

    expect(screen.getByRole("button", { name: "Generate evaluator" })).toBeDisabled();

    await user.type(screen.getByLabelText("Criteria"), "Judge answer quality.");
    expect(screen.getByRole("button", { name: "Generate evaluator" })).not.toBeDisabled();
    expect(generateMock).not.toHaveBeenCalled();
  });
});

// -- Modal chrome ---------------------------------------------------------------

describe("EvaluatorFromDataset chrome", () => {
  it("describes that the shape is derived and the inputs only steer content", async () => {
    await renderReady();

    expect(screen.getByText(/derived|steer/i)).toBeInTheDocument();
  });

  it("keeps the Cancel action wired to onClose from the footer", async () => {
    const user = userEvent.setup();
    const { onClose } = await renderReady();

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onClose).toHaveBeenCalled();
  });
});

// -- Gateway gating -----------------------------------------------------------

describe("EvaluatorFromDataset gateway gating", () => {
  it("disables Generate and shows the shared gateway blocker when the gateway key is unset", async () => {
    useSetupMock.mockReturnValue(makeSetupResult({ gatewayReady: false }));
    const user = userEvent.setup();
    await renderReady();

    await user.type(screen.getByLabelText("Criteria"), "Judge answer quality.");

    expect(screen.getByText(GATEWAY_BLOCKER)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate evaluator" })).toBeDisabled();
    expect(generateMock).not.toHaveBeenCalled();
  });

  it("shows no gateway blocker and governs Generate only by criteria validity when the gateway is ready", async () => {
    useSetupMock.mockReturnValue(makeSetupResult({ gatewayReady: true }));
    const user = userEvent.setup();
    await renderReady();

    expect(screen.queryByText(GATEWAY_BLOCKER)).toBeNull();
    expect(screen.getByRole("button", { name: "Generate evaluator" })).toBeDisabled();

    await user.type(screen.getByLabelText("Criteria"), "Judge answer quality.");
    expect(screen.getByRole("button", { name: "Generate evaluator" })).not.toBeDisabled();
  });
});
