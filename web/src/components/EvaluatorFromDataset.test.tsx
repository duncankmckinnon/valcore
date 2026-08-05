// Seeded generation, dataset -> evaluator direction. This modal derives an evaluator's
// column set from an existing dataset (its columns are the fixed, required set) while the
// user supplies criteria and per-column notes to steer the generated judge. The result is
// an editable draft handed to the version editor; the modal itself persists nothing.
//
// These tests mock only the client module (as the neighbouring component tests do) and
// exercise the real ColumnNotesEditor so the locked-column / no-add-control behaviour is
// verified end to end rather than stubbed.

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import EvaluatorFromDataset from "./EvaluatorFromDataset";
import { ApiError, evaluators } from "../api/client";
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

const generateMock = vi.mocked(evaluators.generate);
const createMock = vi.mocked(evaluators.create);
const createVersionMock = vi.mocked(evaluators.createVersion);

const LABELLED_SCHEMA: LabelSchema = {
  kind: "categorical",
  labels: ["good", "bad"],
  minimum: null,
  maximum: null,
};

// Decision 4: an empty label_schema is legal and means "no ground truth".
const EMPTY_SCHEMA: LabelSchema = {
  kind: "categorical",
  labels: [],
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

  it("sends dataset_id and column_notes and no columns key on submit", async () => {
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
    // Sending an explicit `columns` array alongside `dataset_id` is a server-side error;
    // the shape must come from the dataset alone.
    expect(arg).not.toHaveProperty("columns");
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
