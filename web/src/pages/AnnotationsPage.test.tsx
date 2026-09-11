// web/src/pages/AnnotationsPage.test.tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import AnnotationsPage from "./AnnotationsPage";
import { datasets, labelSets } from "../api/client";
import type { DatasetSummary, LabelSetProgress } from "../api/types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    datasets: { ...actual.datasets, list: vi.fn() },
    labelSets: { ...actual.labelSets, list: vi.fn(), create: vi.fn() },
  };
});

const listDatasetsMock = vi.mocked(datasets.list);
const listLabelSetsMock = vi.mocked(labelSets.list);
const createLabelSetMock = vi.mocked(labelSets.create);

function makeDataset(overrides: Partial<DatasetSummary> = {}): DatasetSummary {
  return {
    id: "d1",
    created_at: "2026-01-01T00:00:00Z",
    name: "reviews",
    description: "",
    columns: ["text"],
    row_count: 10,
    labeled_count: 4,
    ...overrides,
  };
}

function makeLabelSet(overrides: Partial<LabelSetProgress> = {}): LabelSetProgress {
  return {
    id: "ls1",
    created_at: "2026-01-01T00:00:00Z",
    dataset_id: "d1",
    name: "quality",
    description: "",
    kind: "categorical",
    labels: [{ name: "good", description: "" }, { name: "bad", description: "" }],
    minimum: null,
    maximum: null,
    annotated_count: 4,
    row_count: 10,
    ...overrides,
  };
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/annotations" element={<AnnotationsPage />} />
        <Route path="/annotations/:datasetId" element={<AnnotationsPage />} />
        <Route path="/annotations/:datasetId/:labelSetId" element={<div>queue</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AnnotationsPage", () => {
  it("lists datasets and links into each", async () => {
    listDatasetsMock.mockResolvedValue([makeDataset()]);

    renderAt("/annotations");

    expect(await screen.findByText("reviews")).toBeTruthy();
    expect(screen.getByRole("link", { name: "reviews" }).getAttribute("href")).toBe(
      "/annotations/d1",
    );
  });

  it("lists a dataset's label sets with progress", async () => {
    listLabelSetsMock.mockResolvedValue([makeLabelSet()]);

    renderAt("/annotations/d1");

    expect(await screen.findByText("quality")).toBeTruthy();
    expect(screen.getByText("4 / 10 annotated")).toBeTruthy();
    expect(listLabelSetsMock).toHaveBeenCalledWith("d1");
  });

  it("creates a new label set", async () => {
    listLabelSetsMock.mockResolvedValue([]);
    createLabelSetMock.mockResolvedValue({
      id: "ls2",
      created_at: "2026-01-01T00:00:00Z",
      dataset_id: "d1",
      name: "toxicity",
      description: "",
      kind: "categorical",
      labels: [],
      minimum: null,
      maximum: null,
    });

    renderAt("/annotations/d1");
    await screen.findByRole("button", { name: "New label set" });

    fireEvent.click(screen.getByRole("button", { name: "New label set" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "toxicity" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(createLabelSetMock).toHaveBeenCalledWith("d1", {
        name: "toxicity",
        description: "",
        kind: "categorical",
        labels: [],
        minimum: null,
        maximum: null,
      }),
    );
  });
});
