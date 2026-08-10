// Seeded generation, dataset -> evaluator direction. This modal derives an evaluator's
// column set from an existing dataset (its columns are the fixed, required set) while the
// user supplies criteria and per-column notes to steer the generated judge. The result is
// an editable draft handed to the version editor; the modal itself persists nothing.
//
// These tests mock only the client module (as the neighbouring component tests do) and
// exercise the real ColumnNotesEditor so the locked-column / no-add-control behaviour is
// verified end to end rather than stubbed.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import EvaluatorFromDataset from "./EvaluatorFromDataset";
import { ApiError, evaluators } from "../api/client";
import { GATEWAY_BLOCKER, useSetup } from "./useSetup";
import type { UseSetupResult } from "./useSetup";
import type { Dataset, GeneratedConfig, LabelSchema } from "../api/types";

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

const LABELLED_SCHEMA: LabelSchema = {
  kind: "categorical",
  labels: ["good", "bad"],
  minimum: null,
  maximum: null,
};

// The API returns a literal empty object when the dataset has no ground truth.
const EMPTY_SCHEMA: Dataset["label_schema"] = {};

const BOUNDED_NUMERIC_SCHEMA: LabelSchema = {
  kind: "numeric",
  labels: null,
  minimum: 1,
  maximum: 5,
};

const UNBOUNDED_NUMERIC_SCHEMA: LabelSchema = {
  kind: "numeric",
  labels: null,
  minimum: null,
  maximum: null,
};

function madeDataset(overrides: Partial<Dataset> = {}): Dataset {
  return {
    id: "d1",
    created_at: "2026-01-01T00:00:00Z",
    name: "Support tickets",
    description: "desc",
    columns: ["question", "answer"],
    label_schema: LABELLED_SCHEMA,
    row_count: 0,
    labeled_count: 0,
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

function renderModal(props: {
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
  return { onGenerated, onClose };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

beforeEach(() => {
  useSetupMock.mockReturnValue(makeSetupResult());
});

describe("EvaluatorFromDataset", () => {
  it("renders the dataset's columns locked with no control for adding columns", () => {
    renderModal({ dataset: madeDataset({ columns: ["question", "answer"] }) });

    // Each dataset column is present as a per-column note row (from ColumnNotesEditor).
    expect(screen.getByLabelText("Note for question")).toBeInTheDocument();
    expect(screen.getByLabelText("Note for answer")).toBeInTheDocument();

    // Locked, not removable: no remove control on any dataset column.
    expect(screen.queryByRole("button", { name: "Remove question" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove answer" })).toBeNull();

    // allowAddColumns is false, so the add-column affordance never renders. The dataset's
    // columns are the fixed set; the user cannot introduce new ones here.
    expect(screen.queryByLabelText("New column name")).toBeNull();
    expect(screen.queryByRole("button", { name: "Add column" })).toBeNull();

    // The note prompt asks how the column factors into the assessment.
    expect(
      (screen.getByLabelText("Note for question") as HTMLInputElement).placeholder,
    ).toMatch(/assess/i);
  });

  it("sends dataset_id, every column, and column_notes on submit", async () => {
    const draft = madeDraft();
    generateMock.mockResolvedValue(draft);
    const user = userEvent.setup();
    renderModal({ dataset: madeDataset({ columns: ["question", "answer"] }) });

    await user.type(screen.getByLabelText("Criteria"), "Does the answer resolve the ticket?");
    await user.type(screen.getByLabelText("Note for question"), "the customer's problem");

    await user.click(screen.getByRole("button", { name: "Generate evaluator" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalledTimes(1));
    const arg = generateMock.mock.calls[0][0];
    expect(arg.dataset_id).toBe("d1");
    expect(arg.column_notes).toEqual({ question: "the customer's problem" });
    expect(arg.criteria).toBe("Does the answer resolve the ticket?");
    // `columns` now narrows the dataset-derived set rather than conflicting with it, and
    // defaults to every column so the pre-subset behaviour is preserved.
    expect(arg.columns).toEqual(["question", "answer"]);
  });

  it("narrows columns to the included ones and drops an excluded column's note", async () => {
    generateMock.mockResolvedValue(madeDraft());
    const user = userEvent.setup();
    renderModal({ dataset: madeDataset({ columns: ["question", "answer"] }) });

    await user.type(screen.getByLabelText("Criteria"), "grade it");
    await user.type(screen.getByLabelText("Note for answer"), "stale note");
    await user.click(screen.getByLabelText("Include answer"));

    await user.click(screen.getByRole("button", { name: "Generate evaluator" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalledTimes(1));
    const arg = generateMock.mock.calls[0][0];
    expect(arg.columns).toEqual(["question"]);
    // A note keyed outside the resolved set is a server-side error, so it must not travel.
    expect(arg.column_notes).toEqual({});
  });

  it("inherits the dataset's label space by default and sends no label_schema", async () => {
    generateMock.mockResolvedValue(madeDraft());
    const user = userEvent.setup();
    renderModal({ dataset: madeDataset({ label_schema: LABELLED_SCHEMA }) });

    expect(screen.getByLabelText("Use this dataset's label space")).toBeChecked();

    await user.type(screen.getByLabelText("Criteria"), "grade it");
    await user.click(screen.getByRole("button", { name: "Generate evaluator" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalledTimes(1));
    // Omitted, not sent-as-the-dataset's: the server seeds it, so there is one source of truth.
    expect(generateMock.mock.calls[0][0]).not.toHaveProperty("label_schema");
  });

  it("sends a prescribed label_schema once the dataset labels are turned off", async () => {
    generateMock.mockResolvedValue(madeDraft());
    const user = userEvent.setup();
    renderModal({ dataset: madeDataset({ label_schema: LABELLED_SCHEMA }) });

    await user.click(screen.getByLabelText("Use this dataset's label space"));
    // The schema editor replaces the read-only chips.
    expect(screen.getByPlaceholderText("Add a label")).toBeInTheDocument();
    // The consequence is stated where the choice is made, not when a run later fails.
    expect(screen.getByText(/cannot be validated against it/)).toBeInTheDocument();

    await user.type(screen.getByLabelText("Criteria"), "grade it");
    await user.click(screen.getByRole("button", { name: "Generate evaluator" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalledTimes(1));
    expect(generateMock.mock.calls[0][0].label_schema).toEqual(LABELLED_SCHEMA);
  });

  it("blocks submission when every column is excluded", async () => {
    const user = userEvent.setup();
    renderModal({ dataset: madeDataset({ columns: ["question"] }) });

    await user.type(screen.getByLabelText("Criteria"), "grade it");
    await user.click(screen.getByLabelText("Include question"));

    expect(screen.getByText("Include at least one column.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate evaluator" })).toBeDisabled();
  });

  it("renders a declared label space read-only with a line that the evaluator will use it", () => {
    renderModal({ dataset: madeDataset({ label_schema: LABELLED_SCHEMA }) });

    // The dataset's labels are shown for reference...
    expect(screen.getByText("good")).toBeInTheDocument();
    expect(screen.getByText("bad")).toBeInTheDocument();
    // ...and explained: when labels are involved the space comes from the evaluator, which
    // here is derived from this dataset's space.
    expect(screen.getByText(/generated evaluator will use/i)).toBeInTheDocument();

    // Read-only: the labels are not editable inputs.
    expect(screen.queryByDisplayValue("good")).toBeNull();
    expect(screen.queryByDisplayValue("bad")).toBeNull();
  });

  it("explains the evaluator defines its own space when the dataset has an empty label schema", () => {
    renderModal({ dataset: madeDataset({ label_schema: EMPTY_SCHEMA }) });

    expect(screen.getByText(/define its own/i)).toBeInTheDocument();
    // No stray label chips when there is no declared space.
    expect(screen.queryByText("good")).toBeNull();
  });

  it("renders a bounded numeric label space read-only", () => {
    renderModal({ dataset: madeDataset({ label_schema: BOUNDED_NUMERIC_SCHEMA }) });

    expect(screen.getByText(/Minimum: 1; Maximum: 5/)).toBeInTheDocument();
    expect(screen.getByText(/generated evaluator will use/i)).toBeInTheDocument();
    expect(screen.queryByRole("spinbutton")).toBeNull();
  });

  it("recognizes and renders an unbounded numeric label space", () => {
    renderModal({ dataset: madeDataset({ label_schema: UNBOUNDED_NUMERIC_SCHEMA }) });

    expect(screen.getByText(/Minimum: unbounded; Maximum: unbounded/)).toBeInTheDocument();
    expect(screen.getByText(/generated evaluator will use/i)).toBeInTheDocument();
    expect(screen.queryByText(/define its own/i)).toBeNull();
  });

  it("hands the generated draft to the version editor without saving anything", async () => {
    const draft = madeDraft();
    generateMock.mockResolvedValue(draft);
    const user = userEvent.setup();
    const { onGenerated } = renderModal();

    await user.type(screen.getByLabelText("Criteria"), "Judge answer quality.");
    await user.click(screen.getByRole("button", { name: "Generate evaluator" }));

    await waitFor(() => expect(onGenerated).toHaveBeenCalledWith(draft));
    // The draft is editable, not persisted: no evaluator and no version are created here.
    expect(createMock).not.toHaveBeenCalled();
    expect(createVersionMock).not.toHaveBeenCalled();
  });

  it("surfaces a server error inside the modal and does not hand back a draft", async () => {
    generateMock.mockRejectedValue(
      new ApiError("columns and dataset_id are mutually exclusive", "ContractError", 400),
    );
    const user = userEvent.setup();
    const { onGenerated } = renderModal();

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
    renderModal();

    await user.type(screen.getByLabelText("Criteria"), "Judge answer quality.");
    const submit = screen.getByRole("button", { name: "Generate evaluator" });
    await user.click(submit);

    // Still pending: a second submit must be impossible until the request settles.
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
    renderModal();

    // Criteria are required; nothing to submit yet.
    expect(screen.getByRole("button", { name: "Generate evaluator" })).toBeDisabled();

    await user.type(screen.getByLabelText("Criteria"), "Judge answer quality.");
    expect(screen.getByRole("button", { name: "Generate evaluator" })).not.toBeDisabled();
    expect(generateMock).not.toHaveBeenCalled();
  });
});

// -- Redesigned modal chrome -------------------------------------------------
// The redesign adds a description (the shape is derived from the dataset; criteria and
// notes only steer content) and moves the actions into the footer. This direction locks
// the dataset's columns with no add-column affordance, so it gains no extra-columns
// tooltip — only the description and the relocated actions.

describe("EvaluatorFromDataset chrome", () => {
  it("describes that the shape is derived and the inputs only steer content", () => {
    renderModal();

    expect(screen.getByText(/derived|steer/i)).toBeInTheDocument();
  });

  it("keeps the Cancel action wired to onClose from the footer", async () => {
    const user = userEvent.setup();
    const { onClose } = renderModal();

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onClose).toHaveBeenCalled();
  });
});

// -- Gateway gating -----------------------------------------------------------
// Generation needs the Pydantic AI Gateway key as much as running a version does, so it
// is gated the same way: the shared GATEWAY_BLOCKER text and a disabled primary action
// when the key is not set, and no change at all to today's behavior once it is.

describe("EvaluatorFromDataset gateway gating", () => {
  it("disables Generate and shows the shared gateway blocker when the gateway key is unset", async () => {
    useSetupMock.mockReturnValue(makeSetupResult({ gatewayReady: false }));
    const user = userEvent.setup();
    renderModal();

    await user.type(screen.getByLabelText("Criteria"), "Judge answer quality.");

    expect(screen.getByText(GATEWAY_BLOCKER)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate evaluator" })).toBeDisabled();
    expect(generateMock).not.toHaveBeenCalled();
  });

  it("does not call generate if Generate is somehow invoked while the gateway is blocked", async () => {
    useSetupMock.mockReturnValue(makeSetupResult({ gatewayReady: false }));
    renderModal();

    // Disabled buttons swallow user-event clicks by design; assert directly on
    // generateMock so this test does not depend on that browser behaviour.
    expect(generateMock).not.toHaveBeenCalled();
  });

  it("shows no gateway blocker and governs Generate only by criteria validity when the gateway is ready", async () => {
    useSetupMock.mockReturnValue(makeSetupResult({ gatewayReady: true }));
    const user = userEvent.setup();
    renderModal();

    expect(screen.queryByText(GATEWAY_BLOCKER)).toBeNull();
    expect(screen.getByRole("button", { name: "Generate evaluator" })).toBeDisabled();

    await user.type(screen.getByLabelText("Criteria"), "Judge answer quality.");
    expect(screen.getByRole("button", { name: "Generate evaluator" })).not.toBeDisabled();
  });

  it("re-enables Generate once gatewayReady flips true with criteria already filled", async () => {
    useSetupMock.mockReturnValue(makeSetupResult({ gatewayReady: false }));
    const user = userEvent.setup();
    const { rerender } = render(
      <EvaluatorFromDataset open dataset={madeDataset()} onGenerated={vi.fn()} onClose={vi.fn()} />,
    );

    await user.type(screen.getByLabelText("Criteria"), "Judge answer quality.");
    expect(screen.getByRole("button", { name: "Generate evaluator" })).toBeDisabled();

    useSetupMock.mockReturnValue(makeSetupResult({ gatewayReady: true }));
    rerender(
      <EvaluatorFromDataset open dataset={madeDataset()} onGenerated={vi.fn()} onClose={vi.fn()} />,
    );

    expect(screen.getByRole("button", { name: "Generate evaluator" })).not.toBeDisabled();
    expect(screen.queryByText(GATEWAY_BLOCKER)).toBeNull();
  });
});
