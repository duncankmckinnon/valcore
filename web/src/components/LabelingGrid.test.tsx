// web/src/components/LabelingGrid.test.tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import AnnotationQueue from "./LabelingGrid";
import type { Annotation, AnnotationRow, AnnotationRowsPage, LabelSetProgress } from "../api/types";
import { annotations, datasets, labelSets } from "../api/client";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    labelSets: { ...actual.labelSets, list: vi.fn(), rows: vi.fn() },
    annotations: { ...actual.annotations, put: vi.fn(), remove: vi.fn(), accept: vi.fn() },
    datasets: { ...actual.datasets, patchRowData: vi.fn(), deleteRow: vi.fn(), get: vi.fn() },
  };
});

const listLabelSetsMock = vi.mocked(labelSets.list);
const rowsMock = vi.mocked(labelSets.rows);
const putMock = vi.mocked(annotations.put);
const removeMock = vi.mocked(annotations.remove);
const acceptMock = vi.mocked(annotations.accept);
const patchDataMock = vi.mocked(datasets.patchRowData);
const deleteRowMock = vi.mocked(datasets.deleteRow);
const getDatasetMock = vi.mocked(datasets.get);

// The grid now sources its table columns from the dataset's declared schema (fixed in
// this review wave — see LabelingGrid.tsx's `columns` state) rather than from the first
// loaded row's data keys, so every test needs `datasets.get` to resolve with the column
// set its row fixtures use ("text").
beforeEach(() => {
  getDatasetMock.mockResolvedValue({
    id: "d1",
    created_at: "2026-01-01T00:00:00Z",
    name: "dataset",
    description: "",
    columns: ["text"],
  });
});

const LABEL_SET: LabelSetProgress = {
  id: "ls1",
  created_at: "2026-01-01T00:00:00Z",
  dataset_id: "d1",
  name: "quality",
  description: "",
  kind: "categorical",
  labels: [{ name: "good", description: "meets the bar" }, { name: "bad", description: "" }],
  minimum: null,
  maximum: null,
  annotated_count: 0,
  row_count: 1,
};

function makeAnnotation(overrides: Partial<Annotation> = {}): Annotation {
  return {
    id: "a1",
    label_set_id: "ls1",
    dataset_row_id: "r1",
    labels: [],
    value: null,
    suggested_labels: ["good"],
    suggested_value: null,
    source: "generated",
    reasoning: "looks fine",
    description: null,
    ...overrides,
  };
}

function makeRow(overrides: Partial<AnnotationRow> = {}): AnnotationRow {
  return {
    row_id: "r1",
    idx: 0,
    data: { text: "hello world" },
    annotation: makeAnnotation(),
    ...overrides,
  };
}

function page(rows: AnnotationRow[], overrides: Partial<AnnotationRowsPage> = {}): AnnotationRowsPage {
  return {
    rows,
    total: rows.length,
    annotated_count: rows.filter((r) => r.annotation !== null).length,
    limit: 100,
    offset: 0,
    ...overrides,
  };
}

function renderQueue() {
  return render(
    <MemoryRouter>
      <AnnotationQueue datasetId="d1" labelSetId="ls1" />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AnnotationQueue", () => {
  it("renders row data, the suggestion, and the source badge", async () => {
    listLabelSetsMock.mockResolvedValue([LABEL_SET]);
    rowsMock.mockResolvedValue(page([makeRow()]));

    renderQueue();

    expect(await screen.findByDisplayValue("hello world")).toBeTruthy();
    expect(document.querySelector(".suggested-cell span")?.textContent).toBe("good");
    expect(screen.getByText("generated")).toBeTruthy();
  });

  it("toggles a categorical label on and off with number keys (multi-select)", async () => {
    listLabelSetsMock.mockResolvedValue([LABEL_SET]);
    rowsMock.mockResolvedValue(page([makeRow({ annotation: makeAnnotation({ labels: ["good"], description: "looks solid" }) })]));
    putMock.mockResolvedValue(makeAnnotation({ labels: ["good", "bad"], description: "looks solid" }));

    renderQueue();
    await screen.findByDisplayValue("hello world");

    fireEvent.keyDown(window, { key: "2" });

    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("ls1", "r1", {
        labels: ["good", "bad"],
        value: null,
        description: "looks solid",
      }),
    );
  });

  it("accepts the suggestion when 'a' is pressed", async () => {
    listLabelSetsMock.mockResolvedValue([LABEL_SET]);
    rowsMock.mockResolvedValue(page([makeRow()]));
    acceptMock.mockResolvedValue(makeAnnotation({ labels: ["good"], source: "accepted" }));

    renderQueue();
    await screen.findByDisplayValue("hello world");

    fireEvent.keyDown(window, { key: "a" });

    await waitFor(() => expect(acceptMock).toHaveBeenCalledWith("ls1", "r1"));
  });

  it("clears the annotation when 'u' is pressed", async () => {
    listLabelSetsMock.mockResolvedValue([LABEL_SET]);
    rowsMock.mockResolvedValue(page([makeRow({ annotation: makeAnnotation({ labels: ["good"] }) })]));
    removeMock.mockResolvedValue(undefined);

    renderQueue();
    await screen.findByDisplayValue("hello world");

    fireEvent.keyDown(window, { key: "u" });

    await waitFor(() => expect(removeMock).toHaveBeenCalledWith("ls1", "r1"));
  });

  it("rolls the optimistic update back and shows an error when the save fails", async () => {
    listLabelSetsMock.mockResolvedValue([LABEL_SET]);
    rowsMock.mockResolvedValue(page([makeRow({ annotation: makeAnnotation({ labels: ["good"] }) })]));
    putMock.mockRejectedValue(new Error("save failed"));

    renderQueue();
    await screen.findByDisplayValue("hello world");

    fireEvent.keyDown(window, { key: "2" });

    await screen.findByText("save failed");
    await waitFor(() => expect(screen.getByRole("button", { name: "good" }).getAttribute("aria-pressed")).toBe("true"));
  });

  it("saves a changed short cell with only the changed column", async () => {
    listLabelSetsMock.mockResolvedValue([LABEL_SET]);
    rowsMock.mockResolvedValue(page([makeRow()]));
    patchDataMock.mockResolvedValue({ id: "r1", dataset_id: "d1", idx: 0, data: { text: "changed" } });

    renderQueue();
    await screen.findByDisplayValue("hello world");

    const cell = screen.getByLabelText("text") as HTMLInputElement;
    fireEvent.change(cell, { target: { value: "changed" } });
    fireEvent.blur(cell);

    await waitFor(() =>
      expect(patchDataMock).toHaveBeenCalledWith("r1", { data: { text: "changed" } }),
    );
  });

  it("requests the next page with the right offset", async () => {
    listLabelSetsMock.mockResolvedValue([LABEL_SET]);
    rowsMock.mockImplementation(async (_id, params) =>
      page([makeRow({ row_id: params?.offset ? "r2" : "r1" })], {
        total: 250,
        offset: params?.offset ?? 0,
      }),
    );

    renderQueue();
    await screen.findByDisplayValue("hello world");
    expect(rowsMock).toHaveBeenCalledWith("ls1", { limit: 100, offset: 0 });

    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() => expect(rowsMock).toHaveBeenLastCalledWith("ls1", { limit: 100, offset: 100 }));
  });

  it("deletes a row after the confirm dialog is confirmed", async () => {
    listLabelSetsMock.mockResolvedValue([LABEL_SET]);
    rowsMock.mockResolvedValue(page([makeRow()]));
    deleteRowMock.mockResolvedValue(undefined);

    renderQueue();
    await screen.findByDisplayValue("hello world");

    fireEvent.click(screen.getByRole("button", { name: "Delete row 0" }));
    expect(deleteRowMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(deleteRowMock).toHaveBeenCalledWith("r1"));
    await waitFor(() => expect(screen.queryAllByLabelText("text").length).toBe(0));
  });

  it("renders a number input for a numeric label set", async () => {
    const numericSet: LabelSetProgress = { ...LABEL_SET, kind: "numeric", labels: null, minimum: 0, maximum: 1 };
    listLabelSetsMock.mockResolvedValue([numericSet]);
    rowsMock.mockResolvedValue(
      page([makeRow({ annotation: makeAnnotation({ labels: [], value: 0.5, suggested_labels: null, suggested_value: 0.7, description: "initial note" }) })]),
    );
    putMock.mockResolvedValue(makeAnnotation({ value: 0.9, description: "initial note" }));

    renderQueue();
    await screen.findByDisplayValue("hello world");

    const value = screen.getByLabelText("Value") as HTMLInputElement;
    fireEvent.change(value, { target: { value: "0.9" } });
    fireEvent.blur(value);

    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("ls1", "r1", { labels: [], value: 0.9, description: "initial note" }),
    );
  });
});
