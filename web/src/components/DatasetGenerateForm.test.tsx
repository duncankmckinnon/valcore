// Tests for the plain generate flow, where the user defines the label space themselves.
// Coverage focuses on what steers generation: the label mix is opt-in, its labels come from
// the schema the user is editing, and it reaches the API as proportions rather than
// percents; `instructions` are omitted when blank so `description` keeps driving the prompt.

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DatasetGenerateForm from "./DatasetGenerateForm";
import { datasets } from "../api/client";
import type { DatasetCreated } from "../api/types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    datasets: { ...actual.datasets, generate: vi.fn() },
  };
});

const generateMock = vi.mocked(datasets.generate);

function madeCreated(): DatasetCreated {
  return {
    dataset: {
      id: "d-new",
      created_at: "2026-08-05T00:00:00Z",
      name: "Synth",
      description: "",
      columns: ["question"],
      label_schema: { kind: "categorical", labels: ["pass", "fail"], minimum: null, maximum: null },
    },
    row_count: 10,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/** Fill the always-required fields and add two categorical labels to distribute over. */
async function fillBasics(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Name"), "Synth");
  await user.type(screen.getByLabelText("Description"), "support questions");
  await user.type(screen.getByLabelText("Columns (comma separated)"), "question");
  const labelInput = screen.getByPlaceholderText("Add a label");
  await user.type(labelInput, "pass");
  await user.click(screen.getByRole("button", { name: "Add" }));
  await user.type(labelInput, "fail");
  await user.click(screen.getByRole("button", { name: "Add" }));
}

describe("DatasetGenerateForm label mix", () => {
  it("offers no mix editor until the schema has labels", async () => {
    render(<DatasetGenerateForm onCreated={vi.fn()} />);

    // Nothing to distribute over yet, so the control stays out of the way.
    expect(screen.queryByRole("checkbox", { name: /prescribe label distribution/i })).toBeNull();
  });

  it("omits label_mix by default, leaving the distribution to the description", async () => {
    const user = userEvent.setup();
    generateMock.mockResolvedValue(madeCreated());
    render(<DatasetGenerateForm onCreated={vi.fn()} />);

    await fillBasics(user);
    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalled());
    expect(generateMock.mock.calls[0][0].label_mix).toBeUndefined();
  });

  it("sends the mix as proportions once prescribed", async () => {
    const user = userEvent.setup();
    generateMock.mockResolvedValue(madeCreated());
    render(<DatasetGenerateForm onCreated={vi.fn()} />);

    await fillBasics(user);
    await user.click(screen.getByRole("checkbox", { name: /prescribe label distribution/i }));
    const pass = screen.getByLabelText("Percent for pass");
    await user.clear(pass);
    await user.type(pass, "40");
    const fail = screen.getByLabelText("Percent for fail");
    await user.clear(fail);
    await user.type(fail, "60");
    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalled());
    expect(generateMock.mock.calls[0][0].label_mix).toEqual({ pass: 0.4, fail: 0.6 });
  });

  it("blocks Generate while the percents do not total 100", async () => {
    const user = userEvent.setup();
    render(<DatasetGenerateForm onCreated={vi.fn()} />);

    await fillBasics(user);
    await user.click(screen.getByRole("checkbox", { name: /prescribe label distribution/i }));
    const pass = screen.getByLabelText("Percent for pass");
    await user.clear(pass);
    await user.type(pass, "10");

    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
    expect(generateMock).not.toHaveBeenCalled();
  });

  it("drops the mix when the schema switches to numeric", async () => {
    const user = userEvent.setup();
    generateMock.mockResolvedValue(madeCreated());
    render(<DatasetGenerateForm onCreated={vi.fn()} />);

    await fillBasics(user);
    await user.click(screen.getByRole("checkbox", { name: /prescribe label distribution/i }));
    // A mix names labels; a numeric space has none, and the API rejects a mix for one.
    await user.selectOptions(screen.getByLabelText("Label kind"), "numeric");

    expect(screen.queryByRole("checkbox", { name: /prescribe label distribution/i })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Generate" }));
    await waitFor(() => expect(generateMock).toHaveBeenCalled());
    expect(generateMock.mock.calls[0][0].label_mix).toBeUndefined();
  });
});

describe("DatasetGenerateForm instructions", () => {
  it("omits instructions when blank so the description drives generation", async () => {
    // Preserves the behaviour this form had before it grew an instructions box.
    const user = userEvent.setup();
    generateMock.mockResolvedValue(madeCreated());
    render(<DatasetGenerateForm onCreated={vi.fn()} />);

    await fillBasics(user);
    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalled());
    expect(generateMock.mock.calls[0][0].instructions).toBeUndefined();
    expect(generateMock.mock.calls[0][0].description).toBe("support questions");
  });

  it("sends instructions alongside the stored description", async () => {
    const user = userEvent.setup();
    generateMock.mockResolvedValue(madeCreated());
    render(<DatasetGenerateForm onCreated={vi.fn()} />);

    await fillBasics(user);
    await user.type(
      screen.getByLabelText("Instructions"),
      "half should be subtle jailbreaks",
    );
    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalled());
    const payload = generateMock.mock.calls[0][0];
    // The description is still what gets stored on the dataset.
    expect(payload.description).toBe("support questions");
    expect(payload.instructions).toBe("half should be subtle jailbreaks");
  });

  it("trims whitespace-only instructions down to omitted", async () => {
    const user = userEvent.setup();
    generateMock.mockResolvedValue(madeCreated());
    render(<DatasetGenerateForm onCreated={vi.fn()} />);

    await fillBasics(user);
    await user.type(screen.getByLabelText("Instructions"), "   ");
    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalled());
    expect(generateMock.mock.calls[0][0].instructions).toBeUndefined();
  });
});

describe("DatasetGenerateForm column notes", () => {
  it("offers no notes editor until columns are named", async () => {
    const user = userEvent.setup();
    render(<DatasetGenerateForm onCreated={vi.fn()} />);

    expect(screen.queryByLabelText("Note for question")).toBeNull();

    await user.type(screen.getByLabelText("Columns (comma separated)"), "question");

    expect(screen.getByLabelText("Note for question")).toBeInTheDocument();
  });

  it("marks no column as required, since the user owns the column list", async () => {
    const user = userEvent.setup();
    render(<DatasetGenerateForm onCreated={vi.fn()} />);

    await user.type(screen.getByLabelText("Columns (comma separated)"), "question");

    expect(screen.queryByText("required")).toBeNull();
  });

  it("omits column_notes when every note is blank", async () => {
    const user = userEvent.setup();
    generateMock.mockResolvedValue(madeCreated());
    render(<DatasetGenerateForm onCreated={vi.fn()} />);

    await fillBasics(user);
    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalled());
    expect(generateMock.mock.calls[0][0].column_notes).toBeUndefined();
  });

  it("sends only the columns that were actually annotated", async () => {
    const user = userEvent.setup();
    generateMock.mockResolvedValue(madeCreated());
    render(<DatasetGenerateForm onCreated={vi.fn()} />);

    await user.type(screen.getByLabelText("Name"), "Synth");
    await user.type(screen.getByLabelText("Description"), "support questions");
    await user.type(screen.getByLabelText("Columns (comma separated)"), "question, answer");
    await user.type(screen.getByLabelText("Note for question"), "a support ticket");
    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalled());
    // 'answer' was left blank, so it steers nothing and is not sent.
    expect(generateMock.mock.calls[0][0].column_notes).toEqual({
      question: "a support ticket",
    });
  });

  it("prunes a note whose column was removed from the list", async () => {
    const user = userEvent.setup();
    generateMock.mockResolvedValue(madeCreated());
    render(<DatasetGenerateForm onCreated={vi.fn()} />);

    await user.type(screen.getByLabelText("Name"), "Synth");
    await user.type(screen.getByLabelText("Description"), "support questions");
    const columnsInput = screen.getByLabelText("Columns (comma separated)");
    await user.type(columnsInput, "question, answer");
    await user.type(screen.getByLabelText("Note for answer"), "the agent reply");

    // Drop 'answer' again: its note is now orphaned and would fail the server's check
    // for a column the user can no longer see.
    await user.clear(columnsInput);
    await user.type(columnsInput, "question");
    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalled());
    expect(generateMock.mock.calls[0][0].column_notes).toBeUndefined();
  });

  it("restores a note when its column comes back", async () => {
    // Notes live in state rather than being deleted on prune, so a mistaken edit to the
    // column list does not silently destroy the guidance already written.
    const user = userEvent.setup();
    generateMock.mockResolvedValue(madeCreated());
    render(<DatasetGenerateForm onCreated={vi.fn()} />);

    await user.type(screen.getByLabelText("Name"), "Synth");
    await user.type(screen.getByLabelText("Description"), "support questions");
    const columnsInput = screen.getByLabelText("Columns (comma separated)");
    await user.type(columnsInput, "question");
    await user.type(screen.getByLabelText("Note for question"), "a support ticket");

    await user.clear(columnsInput);
    await user.type(columnsInput, "answer");
    await user.clear(columnsInput);
    await user.type(columnsInput, "question");

    expect(screen.getByLabelText("Note for question")).toHaveValue("a support ticket");
  });
});

describe("DatasetGenerateForm prefill", () => {
  it("starts empty when no prefill is given", () => {
    render(<DatasetGenerateForm onCreated={vi.fn()} />);

    expect(screen.getByLabelText("Name")).toHaveValue("");
    expect(screen.getByLabelText("Columns (comma separated)")).toHaveValue("");
    expect(screen.getByLabelText("Row count")).toHaveValue(20);
  });

  it("seeds every field from the prefill", () => {
    render(
      <DatasetGenerateForm
        onCreated={vi.fn()}
        initial={{
          name: "Support QA copy",
          description: "support questions",
          instructions: "be subtle",
          columns: ["question", "answer"],
          columnNotes: { question: "a support ticket" },
          labelSchema: {
            kind: "categorical",
            labels: ["pass", "fail"],
            minimum: null,
            maximum: null,
          },
          labelMix: { pass: 0.25, fail: 0.75 },
          count: 7,
        }}
      />,
    );

    expect(screen.getByLabelText("Name")).toHaveValue("Support QA copy");
    expect(screen.getByLabelText("Description")).toHaveValue("support questions");
    expect(screen.getByLabelText("Instructions")).toHaveValue("be subtle");
    // Columns round-trip through the comma-separated field.
    expect(screen.getByLabelText("Columns (comma separated)")).toHaveValue("question, answer");
    expect(screen.getByLabelText("Note for question")).toHaveValue("a support ticket");
    expect(screen.getByLabelText("Row count")).toHaveValue(7);
    // A stored mix arrives as proportions and is shown as whole percents.
    expect(screen.getByLabelText("Percent for pass")).toHaveValue(25);
    expect(screen.getByLabelText("Percent for fail")).toHaveValue(75);
  });

  it("enables the mix only when the prefill carries one", () => {
    render(
      <DatasetGenerateForm
        onCreated={vi.fn()}
        initial={{
          columns: ["question"],
          labelSchema: {
            kind: "categorical",
            labels: ["pass", "fail"],
            minimum: null,
            maximum: null,
          },
          labelMix: null,
        }}
      />,
    );

    expect(
      screen.getByRole("checkbox", { name: /prescribe label distribution/i }),
    ).not.toBeChecked();
  });

  it("submits the seeded values unchanged", async () => {
    const user = userEvent.setup();
    generateMock.mockResolvedValue(madeCreated());
    render(
      <DatasetGenerateForm
        onCreated={vi.fn()}
        initial={{
          name: "Support QA copy",
          description: "support questions",
          instructions: "be subtle",
          columns: ["question"],
          labelSchema: {
            kind: "categorical",
            labels: ["pass", "fail"],
            minimum: null,
            maximum: null,
          },
          count: 4,
        }}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(generateMock).toHaveBeenCalled());
    const payload = generateMock.mock.calls[0][0];
    expect(payload.name).toBe("Support QA copy");
    expect(payload.instructions).toBe("be subtle");
    expect(payload.columns).toEqual(["question"]);
    expect(payload.count).toBe(4);
  });
});
