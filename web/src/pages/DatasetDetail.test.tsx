import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import DatasetDetail from "./DatasetDetail";
import EvaluatorsPage from "./EvaluatorsPage";
import { api, ApiError, datasets, evaluators } from "../api/client";
import type { Dataset, GeneratedConfig } from "../api/types";

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
    api: vi.fn(),
    datasets: {
      ...actual.datasets,
      get: vi.fn(),
      stats: vi.fn(),
      remove: vi.fn(),
      // The header's Export action opens the real ExportModal, which fetches through this.
      exportFiles: vi.fn(),
    },
    // `create`/`createVersion` are stubbed so a stray persistence call from the modal
    // wiring would be observable: the generate flow must hand back a draft, never save.
    evaluators: {
      ...actual.evaluators,
      list: vi.fn(),
      generate: vi.fn(),
      create: vi.fn(),
      createVersion: vi.fn(),
    },
  };
});

const getMock = vi.mocked(datasets.get);
const statsMock = vi.mocked(datasets.stats);
const removeMock = vi.mocked(datasets.remove);
const generateMock = vi.mocked(evaluators.generate);
const createMock = vi.mocked(evaluators.create);
const createVersionMock = vi.mocked(evaluators.createVersion);
const apiMock = vi.mocked(api);
const listMock = vi.mocked(evaluators.list);
const exportFilesMock = vi.mocked(datasets.exportFiles);

function madeDraft(): GeneratedConfig {
  return {
    name: "Answer quality",
    version_name: "v1",
    instructions: "Judge whether the answer resolves the question.",
    prompt_template: "Q: {question}\nA: {answer}",
    required_columns: ["question", "answer"],
    output_fields: [],
    score_field: "score",
    score_kind: "categorical",
    score_labels: ["good", "bad"],
    score_minimum: null,
    score_maximum: null,
    capabilities: [],
    tools: [],
    rationale: "derived from dataset d1",
  };
}

function madeDataset(): Dataset {
  return {
    id: "d1",
    created_at: "2026-01-01T00:00:00Z",
    name: "My set",
    description: "desc",
    columns: ["question", "answer"],
    label_schema: { kind: "categorical", labels: ["good", "bad"], minimum: null, maximum: null },
    row_count: 5,
    labeled_count: 5,
  };
}

function renderDetail() {
  render(
    <MemoryRouter initialEntries={["/datasets/d1"]}>
      <Routes>
        <Route path="/datasets" element={<p>datasets index</p>} />
        <Route path="/datasets/:id" element={<DatasetDetail datasetId="d1" />} />
        <Route path="/evaluators" element={<EvaluatorsPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  apiMock.mockResolvedValue({
    models: ["model-a"],
    default_model: "model-a",
    tools: [],
    capabilities: [],
  });
  listMock.mockResolvedValue([]);
  getMock.mockResolvedValue(madeDataset());
  statsMock.mockResolvedValue({ total: 5, labeled: 5, unlabeled: 0, label_distribution: {} });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DatasetDetail", () => {
  it("carries the title area in a single level-1 heading over its breadcrumb", async () => {
    renderDetail();

    await screen.findByText("header:question");

    // The breadcrumb back to the list stays alongside the adopted PageHeader.
    expect(screen.getByRole("link", { name: "Datasets" })).toBeTruthy();
    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0].textContent).toBe("My set");
    // The description still renders below the title.
    expect(screen.getByText("desc")).toBeTruthy();
  });

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

  it("opens the Generate evaluator modal, seeded with the dataset's columns", async () => {
    renderDetail();

    await screen.findByText("header:question");
    await userEvent.click(screen.getByRole("button", { name: "Generate evaluator" }));

    // The modal is seeded from this dataset: its columns render as locked note rows.
    expect(await screen.findByLabelText("Note for question")).toBeTruthy();
    expect(screen.getByLabelText("Note for answer")).toBeTruthy();
    // Locked set: no add-column control is offered here.
    expect(screen.queryByLabelText("New column name")).toBeNull();
  });

  it("routes the generated draft to the version editor without persisting it", async () => {
    const draft = madeDraft();
    generateMock.mockResolvedValue(draft);
    renderDetail();

    await screen.findByText("header:question");
    await userEvent.click(screen.getByRole("button", { name: "Generate evaluator" }));

    await userEvent.type(
      await screen.findByLabelText("Criteria"),
      "Does the answer resolve the ticket?",
    );
    // Two controls share this name (the page action and the modal submit); the submit is
    // the one inside the dialog.
    const dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Generate evaluator" }));

    // The production destination consumes router state and populates the existing editor.
    expect(await screen.findByRole("heading", { name: "Answer quality" })).toBeTruthy();
    expect(screen.getByLabelText("Version name")).toHaveValue("v1");
    expect(screen.getByLabelText("Instructions")).toHaveValue(draft.instructions);
    expect(screen.getByLabelText("Prompt template")).toHaveValue(draft.prompt_template);
    // Seeded generation sends dataset_id plus the columns to expose, which defaults to all of
    // them; `columns` narrows the dataset-derived set rather than conflicting with it.
    const arg = generateMock.mock.calls[0][0];
    expect(arg.dataset_id).toBe("d1");
    expect(arg.columns).toEqual(["question", "answer"]);
    // The draft is editable, not saved: nothing was persisted from the modal.
    expect(createMock).not.toHaveBeenCalled();
    expect(createVersionMock).not.toHaveBeenCalled();
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

  it("offers an Export action in the header alongside the other dataset actions", async () => {
    renderDetail();

    await screen.findByText("header:question");

    // The action sits in the header next to Edit / Delete; the modal has not opened yet.
    expect(screen.getByRole("button", { name: "Export" })).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "Export dataset" })).toBeNull();
  });

  it("opens the export modal on click and fetches this dataset's code export", async () => {
    exportFilesMock.mockResolvedValue({ "my_set.py": "# pydantic_evals.Dataset module" });
    renderDetail();

    await screen.findByText("header:question");
    await userEvent.click(screen.getByRole("button", { name: "Export" }));

    // The real ExportModal opens on Code and renders one named block per emitted file.
    expect(await screen.findByRole("dialog", { name: "Export dataset" })).toBeTruthy();
    expect(await screen.findByText("my_set.py")).toBeTruthy();

    // Dataset Code is fetched for this id in the default bundled layout. The dataset page
    // exports the dataset alone, so no evaluator version id is threaded through.
    await waitFor(() => expect(exportFilesMock).toHaveBeenCalledTimes(1));
    const [id, format, opts] = exportFilesMock.mock.calls[0];
    expect(id).toBe("d1");
    expect(format).toBe("code");
    expect(opts.layout).toBe("bundled");
    expect(opts.versionId).toBeUndefined();
  });

  it("unmounts the export modal when it is closed", async () => {
    exportFilesMock.mockResolvedValue({ "my_set.py": "# pydantic_evals.Dataset module" });
    renderDetail();

    await screen.findByText("header:question");
    await userEvent.click(screen.getByRole("button", { name: "Export" }));

    const dialog = await screen.findByRole("dialog", { name: "Export dataset" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Close" }));

    // Gating the modal on local `exporting` state means closing removes it from the tree,
    // matching how EvaluatorDetail tears down its own export modal.
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Export dataset" })).toBeNull(),
    );
  });
});
