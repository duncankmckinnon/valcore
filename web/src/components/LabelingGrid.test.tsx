import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import LabelingGrid from "./LabelingGrid";
import type { DatasetRow, LabelSchema, RowsPage } from "../api/types";
import { datasets } from "../api/client";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    datasets: {
      rows: vi.fn(),
      patchRow: vi.fn(),
      addRows: vi.fn(),
      deleteRow: vi.fn(),
    },
  };
});

const rowsMock = vi.mocked(datasets.rows);
const patchMock = vi.mocked(datasets.patchRow);
const addRowsMock = vi.mocked(datasets.addRows);
const deleteRowMock = vi.mocked(datasets.deleteRow);

const SCHEMA: LabelSchema = { kind: "categorical", labels: ["good", "bad"], minimum: null, maximum: null };

function makeRow(overrides: Partial<DatasetRow> = {}): DatasetRow {
  return {
    id: "r1",
    created_at: "2026-01-01T00:00:00Z",
    dataset_id: "d1",
    idx: 0,
    data: { text: "hello world" },
    label: null,
    suggested_label: { value: "good" },
    label_reasoning: "looks fine",
    label_source: "generated",
    note: null,
    ...overrides,
  };
}

function page(rows: DatasetRow[], overrides: Partial<RowsPage> = {}): RowsPage {
  return { rows, total: rows.length, limit: 100, offset: 0, ...overrides };
}

function renderGrid(
  schema: LabelSchema = SCHEMA,
  options: { onChange?: () => void; columns?: string[] } = {},
) {
  return render(
    <LabelingGrid
      datasetId="d1"
      columns={options.columns ?? ["text"]}
      schema={schema}
      onChange={options.onChange}
    />,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("LabelingGrid", () => {
  it("renders row data, the suggestion, and the source badge", async () => {
    rowsMock.mockResolvedValue(page([makeRow()]));

    renderGrid();

    expect(await screen.findByDisplayValue("hello world")).toBeTruthy();
    expect(document.querySelector(".suggested-cell span")?.textContent).toBe("good");
    expect(screen.getByText("generated")).toBeTruthy();
  });

  it("moves focus with j and k", async () => {
    rowsMock.mockResolvedValue(
      page([makeRow({ id: "r1" }), makeRow({ id: "r2", data: { text: "second" } })]),
    );

    renderGrid();
    await screen.findByDisplayValue("hello world");

    const focusedIndex = () => {
      const selected = document.querySelectorAll('tr[aria-selected="true"]');
      expect(selected.length).toBe(1);
      return Array.from(document.querySelectorAll("tbody tr")).indexOf(selected[0] as Element);
    };

    expect(focusedIndex()).toBe(0);
    fireEvent.keyDown(window, { key: "j" });
    expect(focusedIndex()).toBe(1);
    fireEvent.keyDown(window, { key: "k" });
    expect(focusedIndex()).toBe(0);
  });

  it("accepts the suggestion when 'a' is pressed", async () => {
    rowsMock.mockResolvedValue(page([makeRow()]));
    patchMock.mockResolvedValue(makeRow({ label: { value: "good" }, label_source: "accepted" }));

    renderGrid();
    await screen.findByDisplayValue("hello world");

    fireEvent.keyDown(window, { key: "a" });

    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledWith("r1", { accept_suggestion: true }),
    );
  });

  it("applies the first categorical label when '1' is pressed", async () => {
    rowsMock.mockResolvedValue(page([makeRow()]));
    patchMock.mockResolvedValue(makeRow({ label: { value: "good" }, label_source: "manual" }));

    renderGrid();
    await screen.findByDisplayValue("hello world");

    fireEvent.keyDown(window, { key: "1" });

    await waitFor(() => expect(patchMock).toHaveBeenCalledWith("r1", { label: "good" }));
  });

  it("clears the label when 'u' is pressed", async () => {
    rowsMock.mockResolvedValue(
      page([makeRow({ label: { value: "good" }, label_source: "manual" })]),
    );
    patchMock.mockResolvedValue(makeRow({ label: null, label_source: null }));

    renderGrid();
    await screen.findByDisplayValue("hello world");

    fireEvent.keyDown(window, { key: "u" });

    await waitFor(() => expect(patchMock).toHaveBeenCalledWith("r1", { clear_label: true }));
  });

  it("rolls the optimistic update back and shows an error when patchRow fails", async () => {
    rowsMock.mockResolvedValue(
      page([makeRow({ label: { value: "good" }, label_source: "manual" })]),
    );
    patchMock.mockRejectedValue(new Error("save failed"));

    renderGrid();
    await screen.findByDisplayValue("hello world");

    const select = screen.getByLabelText("Label") as HTMLSelectElement;
    expect(select.value).toBe("good");

    // Press '2' to optimistically switch to "bad"; the save then fails.
    fireEvent.keyDown(window, { key: "2" });

    await screen.findByText("save failed");
    await waitFor(() => expect((screen.getByLabelText("Label") as HTMLSelectElement).value).toBe("good"));
  });

  it("requests the next page with the right offset", async () => {
    rowsMock.mockImplementation(async (_id, params) =>
      page([makeRow({ id: params?.offset ? "r2" : "r1" })], {
        total: 250,
        offset: params?.offset ?? 0,
      }),
    );

    renderGrid();
    await screen.findByDisplayValue("hello world");
    expect(rowsMock).toHaveBeenCalledWith("d1", { limit: 100, offset: 0 });

    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() =>
      expect(rowsMock).toHaveBeenLastCalledWith("d1", { limit: 100, offset: 100 }),
    );
  });

  it("saves a changed short cell with only the changed column", async () => {
    rowsMock.mockResolvedValue(page([makeRow({ data: { text: "hello world" } })]));
    patchMock.mockResolvedValue(makeRow({ data: { text: "changed" } }));

    renderGrid();
    await screen.findByDisplayValue("hello world");

    const cell = screen.getByLabelText("text") as HTMLInputElement;
    fireEvent.change(cell, { target: { value: "changed" } });
    fireEvent.blur(cell);

    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledWith("r1", { data: { text: "changed" } }),
    );
  });

  it("does not save a cell blurred without a change", async () => {
    rowsMock.mockResolvedValue(page([makeRow({ data: { text: "hello world" } })]));

    renderGrid();
    await screen.findByDisplayValue("hello world");

    const cell = screen.getByLabelText("text") as HTMLInputElement;
    fireEvent.blur(cell);

    expect(patchMock).not.toHaveBeenCalled();
  });

  it("rolls a rejected cell edit back to the previous value and shows the error", async () => {
    rowsMock.mockResolvedValue(page([makeRow({ data: { text: "hello world" } })]));
    patchMock.mockRejectedValue(new Error("save failed"));

    renderGrid();
    await screen.findByDisplayValue("hello world");

    const cell = screen.getByLabelText("text") as HTMLInputElement;
    fireEvent.change(cell, { target: { value: "changed" } });
    fireEvent.blur(cell);

    await screen.findByText("save failed");
    await waitFor(() =>
      expect((screen.getByLabelText("text") as HTMLInputElement).value).toBe("hello world"),
    );
  });

  it("renders an editable textarea when a long cell is expanded", async () => {
    const long = "LONGVALUE" + "x".repeat(120);
    rowsMock.mockResolvedValue(page([makeRow({ data: { text: long } })]));

    renderGrid();

    const expander = await screen.findByRole("button", { name: /LONGVALUE/ });
    fireEvent.click(expander);

    const editor = screen.getByRole("textbox", { name: "text" });
    expect(editor.tagName).toBe("TEXTAREA");
    expect((editor as HTMLTextAreaElement).value).toBe(long);
  });

  it("adds a blank row and renders the returned row", async () => {
    const onChange = vi.fn();
    rowsMock.mockResolvedValue(page([makeRow({ data: { text: "hello world" } })]));
    addRowsMock.mockResolvedValue([
      makeRow({
        id: "r-new",
        idx: 1,
        data: { text: "" },
        suggested_label: null,
        label_reasoning: null,
        label_source: null,
      }),
    ]);

    renderGrid(SCHEMA, { onChange });
    await screen.findByDisplayValue("hello world");

    fireEvent.click(screen.getByRole("button", { name: "Add row" }));

    await waitFor(() => expect(addRowsMock).toHaveBeenCalledWith("d1", [{ text: "" }]));
    await waitFor(() => expect(screen.getAllByLabelText("text").length).toBe(2));
    expect(onChange).toHaveBeenCalled();
  });

  it("adds a blank row for a dataset with no columns", async () => {
    rowsMock.mockResolvedValue(page([]));
    addRowsMock.mockResolvedValue([makeRow({ id: "r-new", idx: 0, data: {} })]);

    renderGrid(SCHEMA, { columns: [] });
    await screen.findByRole("button", { name: "Add row" });

    fireEvent.click(screen.getByRole("button", { name: "Add row" }));

    await waitFor(() => expect(addRowsMock).toHaveBeenCalledWith("d1", [{}]));
  });

  it("deletes a row after the confirm dialog is confirmed", async () => {
    const onChange = vi.fn();
    rowsMock.mockResolvedValue(page([makeRow({ idx: 0, data: { text: "hello world" } })]));
    deleteRowMock.mockResolvedValue(undefined);

    renderGrid(SCHEMA, { onChange });
    await screen.findByDisplayValue("hello world");

    fireEvent.click(screen.getByRole("button", { name: "Delete row 0" }));

    // The confirm dialog opens; the destructive call only fires on confirm.
    expect(deleteRowMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(deleteRowMock).toHaveBeenCalledWith("r1"));
    await waitFor(() => expect(screen.queryAllByLabelText("text").length).toBe(0));
    expect(onChange).toHaveBeenCalled();
  });

  it("keeps j/k/1 working when focus is outside any cell", async () => {
    rowsMock.mockResolvedValue(
      page([makeRow({ id: "r1" }), makeRow({ id: "r2", data: { text: "second" } })]),
    );
    patchMock.mockResolvedValue(makeRow({ label: { value: "good" }, label_source: "manual" }));

    renderGrid();
    await screen.findByDisplayValue("hello world");

    const focusedIndex = () => {
      const selected = document.querySelectorAll('tr[aria-selected="true"]');
      expect(selected.length).toBe(1);
      return Array.from(document.querySelectorAll("tbody tr")).indexOf(selected[0] as Element);
    };

    expect(focusedIndex()).toBe(0);
    fireEvent.keyDown(window, { key: "j" });
    expect(focusedIndex()).toBe(1);
    fireEvent.keyDown(window, { key: "k" });
    expect(focusedIndex()).toBe(0);
    fireEvent.keyDown(window, { key: "1" });
    await waitFor(() => expect(patchMock).toHaveBeenCalledWith("r1", { label: "good" }));
  });

  it("does not move rows when a shortcut key is typed inside a cell", async () => {
    rowsMock.mockResolvedValue(
      page([makeRow({ id: "r1" }), makeRow({ id: "r2", data: { text: "second" } })]),
    );

    renderGrid();
    await screen.findByDisplayValue("hello world");

    const focusedIndex = () => {
      const selected = document.querySelectorAll('tr[aria-selected="true"]');
      return Array.from(document.querySelectorAll("tbody tr")).indexOf(selected[0] as Element);
    };
    expect(focusedIndex()).toBe(0);

    const cell = screen.getAllByLabelText("text")[0] as HTMLInputElement;
    fireEvent.keyDown(cell, { key: "j" });

    expect(focusedIndex()).toBe(0);
    expect(patchMock).not.toHaveBeenCalled();
  });
});
