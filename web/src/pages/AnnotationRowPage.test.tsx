// web/src/pages/AnnotationRowPage.test.tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import AnnotationRowPage from "./AnnotationRowPage";
import { annotations, labelSets } from "../api/client";
import type { AnnotationRow, LabelSet } from "../api/types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    labelSets: { ...actual.labelSets, get: vi.fn(), rows: vi.fn() },
    annotations: { ...actual.annotations, put: vi.fn() },
  };
});

const getLabelSetMock = vi.mocked(labelSets.get);
const rowsMock = vi.mocked(labelSets.rows);
const putMock = vi.mocked(annotations.put);

const LABEL_SET: LabelSet = {
  id: "ls1",
  created_at: "2026-01-01T00:00:00Z",
  dataset_id: "d1",
  name: "quality",
  description: "Judge overall quality",
  kind: "categorical",
  labels: [{ name: "good", description: "Meets the bar" }, { name: "bad", description: "Does not" }],
  minimum: null,
  maximum: null,
};

function makeRow(overrides: Partial<AnnotationRow> = {}): AnnotationRow {
  return {
    row_id: "r1",
    idx: 0,
    data: { text: "hello world" },
    annotation: null,
    ...overrides,
  };
}

function renderPage(rowId = "r1", state?: { rows: AnnotationRow[] }) {
  return render(
    <MemoryRouter
      initialEntries={[{ pathname: `/annotations/d1/ls1/rows/${rowId}`, state }]}
    >
      <Routes>
        <Route
          path="/annotations/:datasetId/:labelSetId/rows/:rowId"
          element={<AnnotationRowPageRoute />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

function AnnotationRowPageRoute() {
  return <AnnotationRowPage datasetId="d1" labelSetId="ls1" rowId="r1" />;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AnnotationRowPage", () => {
  it("renders the row's data and each label with its description", async () => {
    getLabelSetMock.mockResolvedValue(LABEL_SET);
    rowsMock.mockResolvedValue({
      rows: [makeRow()],
      total: 1,
      annotated_count: 0,
      limit: 100,
      offset: 0,
    });

    renderPage();

    expect(await screen.findByText("hello world")).toBeTruthy();
    expect(screen.getByText("Meets the bar")).toBeTruthy();
  });

  it("saves the checked labels and description", async () => {
    getLabelSetMock.mockResolvedValue(LABEL_SET);
    rowsMock.mockResolvedValue({
      rows: [makeRow()],
      total: 1,
      annotated_count: 0,
      limit: 100,
      offset: 0,
    });
    putMock.mockResolvedValue({
      id: "a1",
      label_set_id: "ls1",
      dataset_row_id: "r1",
      labels: ["good"],
      value: null,
      suggested_labels: null,
      suggested_value: null,
      source: "manual",
      reasoning: null,
      description: "looks solid",
    });

    renderPage();
    await screen.findByText("hello world");

    fireEvent.click(screen.getByRole("checkbox", { name: /good/ }));
    fireEvent.change(screen.getByLabelText("Description / rationale"), {
      target: { value: "looks solid" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("ls1", "r1", {
        labels: ["good"],
        value: null,
        description: "looks solid",
      }),
    );
  });

  it("disables Prev on the first row and Next on the last", async () => {
    getLabelSetMock.mockResolvedValue(LABEL_SET);
    rowsMock.mockResolvedValue({
      rows: [makeRow({ row_id: "r1", idx: 0 }), makeRow({ row_id: "r2", idx: 1 })],
      total: 2,
      annotated_count: 0,
      limit: 100,
      offset: 0,
    });

    renderPage();
    await screen.findByText("hello world");

    expect((screen.getByRole("button", { name: /Prev/ }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: /Next/ }) as HTMLButtonElement).disabled).toBe(false);
  });
});
