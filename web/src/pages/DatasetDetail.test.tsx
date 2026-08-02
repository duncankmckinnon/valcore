import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import DatasetDetail from "./DatasetDetail";
import { ApiError, datasets } from "../api/client";
import type { Dataset } from "../api/types";

// The settings modal is owned by another task; stub it to report a shape change
// (a new column list) back through `onSaved` when the user saves.
vi.mock("../components/DatasetSettingsModal", () => ({
  default: ({
    open,
    dataset,
    onSaved,
  }: {
    open: boolean;
    dataset: Dataset;
    onSaved: (dataset: Dataset) => void;
    onClose: () => void;
  }) =>
    open ? (
      <div role="dialog" aria-label="Dataset settings">
        <button
          onClick={() => onSaved({ ...dataset, columns: ["query", "answer", "context"] })}
        >
          apply shape change
        </button>
      </div>
    ) : null,
}));

// The labeling grid fetches its own rows; stub it to expose the columns it is
// asked to render so a re-render after a shape change is observable.
vi.mock("../components/LabelingGrid", () => ({
  default: ({ columns }: { columns: string[] }) => (
    <div aria-label="grid">
      {columns.map((column) => (
        <span key={column}>header:{column}</span>
      ))}
    </div>
  ),
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    datasets: { ...actual.datasets, get: vi.fn(), stats: vi.fn(), remove: vi.fn() },
  };
});

const getMock = vi.mocked(datasets.get);
const statsMock = vi.mocked(datasets.stats);
const removeMock = vi.mocked(datasets.remove);

function madeDataset(): Dataset {
  return {
    id: "d1",
    created_at: "2026-01-01T00:00:00Z",
    name: "My set",
    description: "desc",
    columns: ["question", "answer"],
    label_schema: { kind: "categorical", labels: ["good", "bad"], minimum: null, maximum: null },
  };
}

function renderDetail() {
  render(
    <MemoryRouter initialEntries={["/datasets/d1"]}>
      <Routes>
        <Route path="/datasets" element={<p>datasets index</p>} />
        <Route path="/datasets/:id" element={<DatasetDetail datasetId="d1" />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  getMock.mockResolvedValue(madeDataset());
  statsMock.mockResolvedValue({ total: 5, labeled: 5, unlabeled: 0, label_distribution: {} });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DatasetDetail", () => {
  it("opens the settings modal from Edit and re-renders the grid headers after a shape change", async () => {
    renderDetail();

    expect(await screen.findByText("header:question")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = screen.getByRole("dialog", { name: "Dataset settings" });

    await userEvent.click(within(dialog).getByRole("button", { name: "apply shape change" }));

    expect(await screen.findByText("header:query")).toBeTruthy();
    expect(screen.getByText("header:context")).toBeTruthy();
    expect(screen.queryByText("header:question")).toBeNull();
  });

  it("refreshes the stats counts after a shape change clears labels", async () => {
    getMock.mockResolvedValue(madeDataset());
    statsMock
      .mockResolvedValueOnce({ total: 5, labeled: 5, unlabeled: 0, label_distribution: {} })
      .mockResolvedValue({ total: 5, labeled: 1, unlabeled: 4, label_distribution: {} });
    renderDetail();

    await screen.findByText("header:question");
    await waitFor(() => expect(statsMock).toHaveBeenCalledTimes(1));

    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "apply shape change" }));

    await waitFor(() => expect(statsMock).toHaveBeenCalledTimes(2));
    // 4 unlabeled rows is unique to the post-migration stats.
    expect(await screen.findByText("4", { selector: ".stat-value" })).toBeTruthy();
  });

  it("deletes the dataset on confirm and navigates back to the list", async () => {
    removeMock.mockResolvedValue(undefined);
    renderDetail();

    await screen.findByText("header:question");

    await userEvent.click(screen.getByRole("button", { name: "Delete dataset" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /delete/i }));

    await waitFor(() => expect(removeMock).toHaveBeenCalledWith("d1"));
    expect(await screen.findByText("datasets index")).toBeTruthy();
  });

  it("shows the referencing run count and stays on the page when delete is blocked", async () => {
    removeMock.mockRejectedValue(
      new ApiError("blocked", "ReferencedError", 409, { run_count: 2 }),
    );
    renderDetail();

    await screen.findByText("header:question");

    await userEvent.click(screen.getByRole("button", { name: "Delete dataset" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /delete/i }));

    await waitFor(() => expect(removeMock).toHaveBeenCalledWith("d1"));
    expect(await screen.findByText(/2 runs depend on this/i)).toBeTruthy();
    expect(screen.queryByText("datasets index")).toBeNull();
    expect(screen.getByRole("heading", { name: "My set" })).toBeTruthy();
  });
});
