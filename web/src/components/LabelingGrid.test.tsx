import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import LabelingGrid from "./LabelingGrid";
import type { DatasetRow, LabelSchema, RowsPage } from "../api/types";
import { datasets } from "../api/client";

vi.mock("../api/client", () => ({
  datasets: {
    rows: vi.fn(),
    patchRow: vi.fn(),
  },
}));

const rowsMock = vi.mocked(datasets.rows);
const patchMock = vi.mocked(datasets.patchRow);

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

function renderGrid(schema: LabelSchema = SCHEMA) {
  return render(
    <LabelingGrid datasetId="d1" columns={["text"]} schema={schema} />,
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

    expect(await screen.findByText("hello world")).toBeTruthy();
    expect(document.querySelector(".suggested-cell span")?.textContent).toBe("good");
    expect(screen.getByText("generated")).toBeTruthy();
  });

  it("moves focus with j and k", async () => {
    rowsMock.mockResolvedValue(
      page([makeRow({ id: "r1" }), makeRow({ id: "r2", data: { text: "second" } })]),
    );

    renderGrid();
    await screen.findByText("hello world");

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
    await screen.findByText("hello world");

    fireEvent.keyDown(window, { key: "a" });

    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledWith("d1", "r1", { accept_suggestion: true }),
    );
  });

  it("applies the first categorical label when '1' is pressed", async () => {
    rowsMock.mockResolvedValue(page([makeRow()]));
    patchMock.mockResolvedValue(makeRow({ label: { value: "good" }, label_source: "manual" }));

    renderGrid();
    await screen.findByText("hello world");

    fireEvent.keyDown(window, { key: "1" });

    await waitFor(() => expect(patchMock).toHaveBeenCalledWith("d1", "r1", { label: "good" }));
  });

  it("clears the label when 'u' is pressed", async () => {
    rowsMock.mockResolvedValue(
      page([makeRow({ label: { value: "good" }, label_source: "manual" })]),
    );
    patchMock.mockResolvedValue(makeRow({ label: null, label_source: null }));

    renderGrid();
    await screen.findByText("hello world");

    fireEvent.keyDown(window, { key: "u" });

    await waitFor(() => expect(patchMock).toHaveBeenCalledWith("d1", "r1", { label: null }));
  });

  it("rolls the optimistic update back and shows an error when patchRow fails", async () => {
    rowsMock.mockResolvedValue(
      page([makeRow({ label: { value: "good" }, label_source: "manual" })]),
    );
    patchMock.mockRejectedValue(new Error("save failed"));

    renderGrid();
    await screen.findByText("hello world");

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
    await screen.findByText("hello world");
    expect(rowsMock).toHaveBeenCalledWith("d1", { limit: 100, offset: 0 });

    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() =>
      expect(rowsMock).toHaveBeenLastCalledWith("d1", { limit: 100, offset: 100 }),
    );
  });
});
