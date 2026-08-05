import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ColumnNotesEditor } from "./ColumnNotesEditor";

const PLACEHOLDER = "What should this column contain?";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// A controlled harness so successive keystrokes accumulate: each onChangeNotes feeds the
// next render's value, mirroring how the seeded-generation modals wire the component up.
function renderStateful(
  props: {
    lockedColumns?: string[];
    extraColumns?: string[];
    notes?: Record<string, string>;
    allowAddColumns?: boolean;
  },
  spies: {
    onChangeNotes?: (notes: Record<string, string>) => void;
    onChangeExtraColumns?: (columns: string[]) => void;
  } = {},
) {
  function Harness() {
    const [notes, setNotes] = useState<Record<string, string>>(props.notes ?? {});
    const [extraColumns, setExtraColumns] = useState<string[]>(props.extraColumns ?? []);
    return (
      <ColumnNotesEditor
        lockedColumns={props.lockedColumns ?? []}
        extraColumns={extraColumns}
        notes={notes}
        notePlaceholder={PLACEHOLDER}
        allowAddColumns={props.allowAddColumns ?? true}
        onChangeNotes={(next) => {
          spies.onChangeNotes?.(next);
          setNotes(next);
        }}
        onChangeExtraColumns={(next) => {
          spies.onChangeExtraColumns?.(next);
          setExtraColumns(next);
        }}
      />
    );
  }
  render(<Harness />);
}

describe("ColumnNotesEditor", () => {
  it("renders locked columns with a note input but no remove control", () => {
    render(
      <ColumnNotesEditor
        lockedColumns={["question", "answer"]}
        extraColumns={[]}
        notes={{}}
        notePlaceholder={PLACEHOLDER}
        allowAddColumns={true}
        onChangeNotes={vi.fn()}
        onChangeExtraColumns={vi.fn()}
      />,
    );

    expect(screen.getByText("question")).toBeInTheDocument();
    expect(screen.getByLabelText("Note for question")).toBeInTheDocument();
    expect(screen.getByLabelText("Note for answer")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove question" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove answer" })).toBeNull();
  });

  it("renders extra columns with both a note input and a remove control", () => {
    render(
      <ColumnNotesEditor
        lockedColumns={["question"]}
        extraColumns={["context"]}
        notes={{}}
        notePlaceholder={PLACEHOLDER}
        allowAddColumns={true}
        onChangeNotes={vi.fn()}
        onChangeExtraColumns={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("Note for context")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove context" })).toBeInTheDocument();
  });

  it("uses the provided placeholder on the note inputs", () => {
    render(
      <ColumnNotesEditor
        lockedColumns={["question"]}
        extraColumns={[]}
        notes={{}}
        notePlaceholder={PLACEHOLDER}
        allowAddColumns={true}
        onChangeNotes={vi.fn()}
        onChangeExtraColumns={vi.fn()}
      />,
    );

    expect((screen.getByLabelText("Note for question") as HTMLInputElement).placeholder).toBe(
      PLACEHOLDER,
    );
  });

  it("calls onChangeNotes with the merged record when a note is typed", async () => {
    const onChangeNotes = vi.fn();
    const user = userEvent.setup();
    // A pre-existing note on another column must survive the edit.
    renderStateful(
      { lockedColumns: ["question", "answer"], notes: { answer: "keep me" } },
      { onChangeNotes },
    );

    await user.type(screen.getByLabelText("Note for question"), "cite sources");

    expect(onChangeNotes).toHaveBeenLastCalledWith({
      answer: "keep me",
      question: "cite sources",
    });
  });

  it("calls onChangeExtraColumns with the new name appended", async () => {
    const onChangeExtraColumns = vi.fn();
    const user = userEvent.setup();
    renderStateful({ lockedColumns: ["question"], extraColumns: ["context"] }, { onChangeExtraColumns });

    await user.type(screen.getByLabelText("New column name"), "citation");
    await user.click(screen.getByRole("button", { name: "Add column" }));

    expect(onChangeExtraColumns).toHaveBeenCalledWith(["context", "citation"]);
  });

  it("does not add a name that already exists in either list", async () => {
    const onChangeExtraColumns = vi.fn();
    const user = userEvent.setup();
    render(
      <ColumnNotesEditor
        lockedColumns={["question"]}
        extraColumns={["context"]}
        notes={{}}
        notePlaceholder={PLACEHOLDER}
        allowAddColumns={true}
        onChangeNotes={vi.fn()}
        onChangeExtraColumns={onChangeExtraColumns}
      />,
    );

    // Duplicate of a locked column.
    await user.type(screen.getByLabelText("New column name"), "question");
    await user.click(screen.getByRole("button", { name: "Add column" }));
    expect(onChangeExtraColumns).not.toHaveBeenCalled();

    // Duplicate of an existing extra column.
    await user.clear(screen.getByLabelText("New column name"));
    await user.type(screen.getByLabelText("New column name"), "context");
    await user.click(screen.getByRole("button", { name: "Add column" }));
    expect(onChangeExtraColumns).not.toHaveBeenCalled();
  });

  it("does not add a blank or whitespace-only name", async () => {
    const onChangeExtraColumns = vi.fn();
    const user = userEvent.setup();
    render(
      <ColumnNotesEditor
        lockedColumns={["question"]}
        extraColumns={[]}
        notes={{}}
        notePlaceholder={PLACEHOLDER}
        allowAddColumns={true}
        onChangeNotes={vi.fn()}
        onChangeExtraColumns={onChangeExtraColumns}
      />,
    );

    await user.type(screen.getByLabelText("New column name"), "   ");
    await user.click(screen.getByRole("button", { name: "Add column" }));

    expect(onChangeExtraColumns).not.toHaveBeenCalled();
  });

  it("drops both the column and its note when an extra column is removed", async () => {
    const onChangeExtraColumns = vi.fn();
    const onChangeNotes = vi.fn();
    const user = userEvent.setup();
    render(
      <ColumnNotesEditor
        lockedColumns={["question"]}
        extraColumns={["context"]}
        notes={{ question: "keep", context: "drop" }}
        notePlaceholder={PLACEHOLDER}
        allowAddColumns={true}
        onChangeNotes={onChangeNotes}
        onChangeExtraColumns={onChangeExtraColumns}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Remove context" }));

    expect(onChangeExtraColumns).toHaveBeenCalledWith([]);
    expect(onChangeNotes).toHaveBeenCalledWith({ question: "keep" });
  });

  it("renders no add control when allowAddColumns is false", () => {
    render(
      <ColumnNotesEditor
        lockedColumns={["question"]}
        extraColumns={["context"]}
        notes={{}}
        notePlaceholder={PLACEHOLDER}
        allowAddColumns={false}
        onChangeNotes={vi.fn()}
        onChangeExtraColumns={vi.fn()}
      />,
    );

    expect(screen.queryByLabelText("New column name")).toBeNull();
    expect(screen.queryByRole("button", { name: "Add column" })).toBeNull();
  });
});
