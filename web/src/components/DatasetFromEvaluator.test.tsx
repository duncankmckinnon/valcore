// Tests for the "Generate dataset" modal launched from the evaluator detail page.
// The modal derives its column shape from an evaluator version (locked required
// columns the user cannot remove) and asks the model to synthesise rows.
//
// The API client is mocked the way the neighbouring form tests mock it. The
// per-column note UI (ColumnNotesEditor) is stubbed to a controllable marker so
// these tests exercise DatasetFromEvaluator's own wiring — what it locks, what it
// puts in the payload, and how it reacts to the label toggle — rather than the
// editor's internals.

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import DatasetFromEvaluator from "./DatasetFromEvaluator";
import { ApiError } from "../api/client";
import type { DatasetCreated, EvaluatorVersion } from "../api/types";

// Hoisted so the mock factory (evaluated during import) can capture the same spy
// the tests assert against.
const generateMock = vi.hoisted(() => vi.fn());

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    datasets: { ...actual.datasets, generateFromVersion: generateMock },
  };
});

const navigate = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

// Stub of the fully-controlled ColumnNotesEditor. It echoes the props that matter
// to these tests (the locked columns, the extras, whether adding is allowed) and
// exposes two buttons that drive its controlled callbacks, standing in for a user
// adding an extra column and typing a per-column note.
vi.mock("./ColumnNotesEditor", () => ({
  default: ({
    lockedColumns,
    extraColumns,
    notes,
    allowAddColumns,
    notePlaceholder,
    onChangeNotes,
    onChangeExtraColumns,
  }: {
    lockedColumns: string[];
    extraColumns: string[];
    notes: Record<string, string>;
    allowAddColumns: boolean;
    notePlaceholder?: string;
    onChangeNotes: (notes: Record<string, string>) => void;
    onChangeExtraColumns: (columns: string[]) => void;
  }) => (
    <div data-testid="column-notes-editor">
      <span data-testid="allow-add">{String(allowAddColumns)}</span>
      <span data-testid="note-placeholder">{notePlaceholder}</span>
      {lockedColumns.map((column) => (
        <span key={column} data-testid="locked-column">
          {column}
        </span>
      ))}
      {extraColumns.map((column) => (
        <span key={column} data-testid="extra-column">
          {column}
        </span>
      ))}
      <button type="button" onClick={() => onChangeExtraColumns([...extraColumns, "context"])}>
        add extra column
      </button>
      <button type="button" onClick={() => onChangeNotes({ ...notes, answer: "the assistant reply" })}>
        set answer note
      </button>
    </div>
  ),
}));

function makeVersion(overrides: Partial<EvaluatorVersion> = {}): EvaluatorVersion {
  return {
    id: "v1",
    created_at: "2026-07-01T00:00:00Z",
    evaluator_id: "e1",
    version_name: "v1",
    notes: "",
    frozen: false,
    model: "model-a",
    instructions: "Judge it.",
    prompt_template: "{question} {answer}",
    required_columns: ["question", "answer"],
    output_fields: [],
    score_field: "verdict",
    score_kind: "categorical",
    score_labels: ["accurate", "inaccurate"],
    score_minimum: null,
    score_maximum: null,
    capabilities: [],
    tools: [],
    ...overrides,
  };
}

function madeCreated(): DatasetCreated {
  return {
    dataset: {
      id: "d-new",
      created_at: "2026-08-04T00:00:00Z",
      name: "Support QA",
      description: "",
      columns: ["question", "answer"],
      label_schema: { kind: "categorical", labels: ["accurate", "inaccurate"], minimum: null, maximum: null },
      row_count: 5,
      labeled_count: 0,
    },
    row_count: 5,
  };
}

function renderModal(overrides: Partial<React.ComponentProps<typeof DatasetFromEvaluator>> = {}) {
  const props = {
    open: true,
    version: makeVersion(),
    maxCount: 200,
    onClose: vi.fn(),
    ...overrides,
  };
  render(
    <MemoryRouter>
      <DatasetFromEvaluator {...props} />
    </MemoryRouter>,
  );
  return props;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DatasetFromEvaluator", () => {
  it("locks the selected version's required columns and allows adding extras", () => {
    renderModal({ version: makeVersion({ required_columns: ["question", "answer"] }) });

    expect(screen.getAllByTestId("locked-column").map((el) => el.textContent)).toEqual([
      "question",
      "answer",
    ]);
    expect(screen.getByTestId("allow-add").textContent).toBe("true");
    // The placeholder should prompt the user for the column's contents.
    expect(screen.getByTestId("note-placeholder").textContent).toMatch(/contain/i);
  });

  it("posts the version id, the extra columns, and the notes record", async () => {
    generateMock.mockResolvedValue(madeCreated());
    const user = userEvent.setup();
    renderModal();

    await user.type(screen.getByLabelText("Dataset name"), "Support QA");
    const count = screen.getByLabelText("Row count");
    await user.clear(count);
    await user.type(count, "5");
    await user.click(screen.getByRole("button", { name: "add extra column" }));
    await user.click(screen.getByRole("button", { name: "set answer note" }));
    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalledOnce());
    expect(generateMock).toHaveBeenCalledWith(
      expect.objectContaining({
        version_id: "v1",
        name: "Support QA",
        extra_columns: ["context"],
        column_notes: { answer: "the assistant reply" },
        count: 5,
      }),
    );
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/datasets/d-new"));
  });

  it("shows the label space and a guidance field when labels are on by default", () => {
    renderModal();

    expect(screen.getByRole("checkbox", { name: "Include suggested labels" })).toBeChecked();
    expect(screen.getByLabelText("Label guidance")).toBeTruthy();
    // The version's label space is shown read-only — its values render as text.
    expect(screen.getByText("accurate")).toBeTruthy();
    expect(screen.getByText("inaccurate")).toBeTruthy();
  });

  it("hides the guidance field and omits label_guidance when labels are toggled off", async () => {
    generateMock.mockResolvedValue(madeCreated());
    const user = userEvent.setup();
    renderModal();

    await user.type(screen.getByLabelText("Dataset name"), "Support QA");
    await user.click(screen.getByRole("checkbox", { name: "Include suggested labels" }));

    expect(screen.queryByLabelText("Label guidance")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalledOnce());
    const [payload] = generateMock.mock.calls[0];
    expect(payload.include_labels).toBe(false);
    expect(payload).not.toHaveProperty("label_guidance");
  });

  it("sends include_labels and the guidance text when labels are on", async () => {
    generateMock.mockResolvedValue(madeCreated());
    const user = userEvent.setup();
    renderModal();

    await user.type(screen.getByLabelText("Dataset name"), "Support QA");
    await user.type(screen.getByLabelText("Label guidance"), "Prefer strict grading.");
    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalledOnce());
    const [payload] = generateMock.mock.calls[0];
    expect(payload.include_labels).toBe(true);
    expect(payload.label_guidance).toBe("Prefer strict grading.");
  });

  it("renders the server error in the modal and keeps the form filled in", async () => {
    generateMock.mockRejectedValue(new ApiError("Generation failed", "ContractError", 422));
    const user = userEvent.setup();
    const props = renderModal();

    await user.type(screen.getByLabelText("Dataset name"), "Support QA");
    await user.click(screen.getByRole("button", { name: "Generate" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Generation failed");
    // The form keeps its values so the user can correct and retry.
    expect(screen.getByLabelText("Dataset name")).toHaveValue("Support QA");
    expect(navigate).not.toHaveBeenCalled();
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it("disables submit while the request is in flight", async () => {
    let resolve: (value: DatasetCreated) => void = () => {};
    generateMock.mockReturnValue(
      new Promise<DatasetCreated>((res) => {
        resolve = res;
      }),
    );
    const user = userEvent.setup();
    renderModal();

    await user.type(screen.getByLabelText("Dataset name"), "Support QA");
    const submit = screen.getByRole("button", { name: "Generate" });
    await user.click(submit);

    expect(submit).toBeDisabled();

    resolve(madeCreated());
    await waitFor(() => expect(navigate).toHaveBeenCalled());
  });

  it("blocks submission when the count exceeds the server maximum", async () => {
    const user = userEvent.setup();
    renderModal({ maxCount: 50 });

    await user.type(screen.getByLabelText("Dataset name"), "Support QA");
    const count = screen.getByLabelText("Row count");
    await user.clear(count);
    await user.type(count, "999");
    await user.click(screen.getByRole("button", { name: "Generate" }));

    expect(generateMock).not.toHaveBeenCalled();
    // The blocking message names the maximum the user must stay within.
    expect(screen.getByText(/50/)).toBeTruthy();
  });
});

// -- Prescribed label distribution -------------------------------------------

describe("label mix", () => {
  it("omits label_mix by default, leaving the distribution to the instructions", async () => {
    const user = userEvent.setup();
    generateMock.mockResolvedValue(madeCreated());
    renderModal();

    await user.type(screen.getByLabelText("Dataset name"), "Support QA");
    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalled());
    expect(generateMock.mock.calls[0][0].label_mix).toBeUndefined();
  });

  it("sends the mix as proportions once prescribed", async () => {
    const user = userEvent.setup();
    generateMock.mockResolvedValue(madeCreated());
    renderModal();

    await user.type(screen.getByLabelText("Dataset name"), "Support QA");
    await user.click(screen.getByRole("checkbox", { name: /prescribe label distribution/i }));
    const accurate = screen.getByLabelText("Percent for accurate");
    await user.clear(accurate);
    await user.type(accurate, "25");
    const inaccurate = screen.getByLabelText("Percent for inaccurate");
    await user.clear(inaccurate);
    await user.type(inaccurate, "75");
    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalled());
    // Percents at the UI boundary, proportions on the wire.
    expect(generateMock.mock.calls[0][0].label_mix).toEqual({
      accurate: 0.25,
      inaccurate: 0.75,
    });
  });

  it("blocks Generate while the percents do not total 100", async () => {
    const user = userEvent.setup();
    renderModal();

    await user.type(screen.getByLabelText("Dataset name"), "Support QA");
    await user.click(screen.getByRole("checkbox", { name: /prescribe label distribution/i }));
    const accurate = screen.getByLabelText("Percent for accurate");
    await user.clear(accurate);
    await user.type(accurate, "10");

    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
    expect(generateMock).not.toHaveBeenCalled();
  });

  it("hides the mix editor when suggested labels are switched off", async () => {
    const user = userEvent.setup();
    renderModal();

    await user.click(screen.getByRole("checkbox", { name: /include suggested labels/i }));

    // The API rejects a mix without labels, so the control must not be reachable.
    expect(screen.queryByRole("checkbox", { name: /prescribe label distribution/i })).toBeNull();
  });

  it("drops a prescribed mix when labels are switched off afterwards", async () => {
    const user = userEvent.setup();
    generateMock.mockResolvedValue(madeCreated());
    renderModal();

    await user.type(screen.getByLabelText("Dataset name"), "Support QA");
    await user.click(screen.getByRole("checkbox", { name: /prescribe label distribution/i }));
    // Turning labels off hides the editor; the stale flag must not reach the payload,
    // which the API would reject with a 422.
    await user.click(screen.getByRole("checkbox", { name: /include suggested labels/i }));
    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalled());
    expect(generateMock.mock.calls[0][0].label_mix).toBeUndefined();
    expect(generateMock.mock.calls[0][0].include_labels).toBe(false);
  });

  it("offers no mix editor for a numeric score space", () => {
    // A mix names labels; the API rejects one for numeric bounds.
    renderModal({
      version: makeVersion({
        score_kind: "numeric",
        score_labels: null,
        score_minimum: 0,
        score_maximum: 5,
      }),
    });

    expect(screen.queryByRole("checkbox", { name: /prescribe label distribution/i })).toBeNull();
  });
});

// -- Redesigned modal chrome -------------------------------------------------
// The redesign adds a description (the shape is derived from the evaluator; instructions
// only steer content), a tooltip explaining the extra-columns field, and moves the
// actions into the footer.

describe("DatasetFromEvaluator chrome", () => {
  it("describes that the shape is derived and instructions only steer content", () => {
    renderModal();

    expect(screen.getByText(/derived|steer/i)).toBeInTheDocument();
  });

  it("keeps the Cancel action wired to onClose from the footer", async () => {
    const user = userEvent.setup();
    const props = renderModal();

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(props.onClose).toHaveBeenCalled();
  });

  it("explains via a tooltip that extra columns are never invented by the model", async () => {
    const user = userEvent.setup();
    renderModal();

    await user.click(screen.getByRole("button", { name: /more information/i }));

    expect(screen.getByRole("tooltip").textContent).toMatch(/invent|named explicitly/i);
  });
});
