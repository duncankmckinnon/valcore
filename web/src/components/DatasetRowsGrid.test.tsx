// web/src/components/DatasetRowsGrid.test.tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DatasetRowsGrid from "./DatasetRowsGrid";
import type { DatasetRow, RowsPage } from "../api/types";
import { datasets } from "../api/client";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    datasets: {
      ...actual.datasets,
      rows: vi.fn(),
      addRows: vi.fn(),
      deleteRow: vi.fn(),
      patchRowData: vi.fn(),
    },
  };
});

const rowsMock = vi.mocked(datasets.rows);
const addRowsMock = vi.mocked(datasets.addRows);
const deleteRowMock = vi.mocked(datasets.deleteRow);
const patchRowDataMock = vi.mocked(datasets.patchRowData);

function makeRow(overrides: Partial<DatasetRow> = {}): DatasetRow {
  return {
    id: "r1",
    dataset_id: "d1",
    idx: 0,
    data: { question: "what is pydantic?", answer: "a data validation library" },
    ...overrides,
  };
}

function page(rows: DatasetRow[], overrides: Partial<RowsPage> = {}): RowsPage {
  return { rows, total: rows.length, limit: 100, offset: 0, ...overrides };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DatasetRowsGrid", () => {
  it("renders each row's data in editable cells, one per column", async () => {
    rowsMock.mockResolvedValue(page([makeRow()]));

    render(<DatasetRowsGrid datasetId="d1" columns={["question", "answer"]} />);

    expect(await screen.findByDisplayValue("what is pydantic?")).toBeTruthy();
    expect(screen.getByDisplayValue("a data validation library")).toBeTruthy();
  });

  it("saves an edited cell via patchRowData on blur", async () => {
    rowsMock.mockResolvedValue(page([makeRow()]));
    patchRowDataMock.mockResolvedValue(
      makeRow({ data: { question: "what is pydantic ai?", answer: "a data validation library" } }),
    );

    render(<DatasetRowsGrid datasetId="d1" columns={["question", "answer"]} />);
    const input = await screen.findByDisplayValue("what is pydantic?");
    await userEvent.clear(input);
    await userEvent.type(input, "what is pydantic ai?");
    await userEvent.tab();

    await waitFor(() =>
      expect(patchRowDataMock).toHaveBeenCalledWith("r1", { data: { question: "what is pydantic ai?" } }),
    );
  });

  it("adds a blank row via the Add row button", async () => {
    rowsMock.mockResolvedValue(page([]));
    addRowsMock.mockResolvedValue([makeRow({ id: "r2", data: { question: "", answer: "" } })]);

    render(<DatasetRowsGrid datasetId="d1" columns={["question", "answer"]} />);
    await waitFor(() => expect(rowsMock).toHaveBeenCalled());

    await userEvent.click(screen.getByRole("button", { name: "Add row" }));

    await waitFor(() =>
      expect(addRowsMock).toHaveBeenCalledWith("d1", [{ question: "", answer: "" }]),
    );
    expect(await screen.findAllByLabelText("question")).toHaveLength(1);
  });

  it("deletes a row after confirming", async () => {
    rowsMock.mockResolvedValue(page([makeRow()]));
    deleteRowMock.mockResolvedValue(undefined);

    render(<DatasetRowsGrid datasetId="d1" columns={["question", "answer"]} />);
    await screen.findByDisplayValue("what is pydantic?");

    await userEvent.click(screen.getByRole("button", { name: "Delete row 0" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /delete/i }));

    await waitFor(() => expect(deleteRowMock).toHaveBeenCalledWith("r1"));
    await waitFor(() => expect(screen.queryByDisplayValue("what is pydantic?")).toBeNull());
  });

  it("calls onChange after a row is added or deleted", async () => {
    rowsMock.mockResolvedValue(page([makeRow()]));
    deleteRowMock.mockResolvedValue(undefined);
    const onChange = vi.fn();

    render(<DatasetRowsGrid datasetId="d1" columns={["question", "answer"]} onChange={onChange} />);
    await screen.findByDisplayValue("what is pydantic?");

    await userEvent.click(screen.getByRole("button", { name: "Delete row 0" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /delete/i }));

    await waitFor(() => expect(onChange).toHaveBeenCalled());
  });
});
