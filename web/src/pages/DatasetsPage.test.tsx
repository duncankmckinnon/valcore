import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import DatasetsPage from "./DatasetsPage";
import { datasets, labelSets, setup } from "../api/client";
import type { DatasetSummary, LabelSetProgress, SetupStatus } from "../api/types";

// The four creation paths are owned by other tasks; stub each so it reports a
// distinct id back through its `onCreated` prop. Blank and Generate hand back a
// dataset id string; Upload hands back a `DatasetCreated` envelope.
vi.mock("../components/DatasetBlankForm", () => ({
  default: ({ onCreated }: { onCreated: (id: string) => void }) => (
    <button onClick={() => onCreated("blank-1")}>blank creates</button>
  ),
}));
vi.mock("../components/DatasetGenerateForm", () => ({
  default: ({
    onCreated,
    initial,
  }: {
    onCreated: (id: string) => void;
    initial?: { labelSchema?: unknown };
  }) => (
    <div>
      <button onClick={() => onCreated("gen-1")}>generate creates</button>
      {initial?.labelSchema !== undefined && (
        <pre data-testid="generate-seed-schema">{JSON.stringify(initial.labelSchema)}</pre>
      )}
    </div>
  ),
}));
vi.mock("../components/DatasetUpload", () => ({
  default: ({ onCreated }: { onCreated: (created: { dataset: { id: string } }) => void }) => (
    <button onClick={() => onCreated({ dataset: { id: "upload-1" } } as never)}>upload creates</button>
  ),
}));
vi.mock("../components/DatasetLogfireForm", () => ({
  default: ({ onCreated }: { onCreated: (id: string) => void }) => (
    <button onClick={() => onCreated("logfire-1")}>logfire creates</button>
  ),
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    datasets: { ...actual.datasets, list: vi.fn(), stats: vi.fn(), generation: vi.fn() },
    labelSets: { ...actual.labelSets, list: vi.fn() },
    setup: { ...actual.setup, get: vi.fn() },
  };
});

const listMock = vi.mocked(datasets.list);
const statsMock = vi.mocked(datasets.stats);
const generationMock = vi.mocked(datasets.generation);
const listLabelSetsMock = vi.mocked(labelSets.list);
const setupGet = vi.mocked(setup.get);

const EMPTY_SETUP: SetupStatus = {
  keys: [],
  default_model: "gateway/anthropic:claude-sonnet-5",
  local_cli_default: null,
  local_cli_options: ["claude", "codex", "cursor"],
  logfire_explore_url: null,
  logfire_traces_url: null,
  logfire_datasets_url: null,
};

// A dataset as it now arrives from `datasets.list()` — carrying the per-dataset
// `row_count` and `labeled_count` the summary strip and table columns read.
function madeDataset(overrides: Partial<DatasetSummary> = {}): DatasetSummary {
  return {
    id: "d1",
    created_at: "2026-01-01T00:00:00Z",
    name: "Alpha",
    description: "",
    columns: ["question", "answer"],
    row_count: 0,
    labeled_count: 0,
    ...overrides,
  };
}

// A sentinel route so navigation to /datasets/:id is observable as text.
function LocationProbe() {
  const location = useLocation();
  return <p>at {location.pathname}</p>;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/datasets"]}>
      <Routes>
        <Route path="/datasets" element={<DatasetsPage />} />
        <Route path="/datasets/:id" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  listMock.mockResolvedValue([]);
  statsMock.mockResolvedValue({ total: 0, labeled: 0, unlabeled: 0, label_distribution: {} });
  generationMock.mockResolvedValue(null);
  setupGet.mockResolvedValue(EMPTY_SETUP);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DatasetsPage", () => {
  it("offers a single New dataset button instead of separate Upload and Generate buttons", async () => {
    renderPage();

    await waitFor(() => expect(listMock).toHaveBeenCalled());

    expect(screen.getByRole("button", { name: "New dataset" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Upload" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Generate" })).toBeNull();
  });

  it("opens the creation modal on the Blank tab by default", async () => {
    renderPage();

    await userEvent.click(screen.getByRole("button", { name: "New dataset" }));

    const blankTab = screen.getByRole("tab", { name: "Blank" });
    expect(blankTab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "blank creates" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "upload creates" })).toBeNull();
    expect(screen.queryByRole("button", { name: "generate creates" })).toBeNull();
  });

  it("renders the upload and generate forms when their tabs are selected", async () => {
    renderPage();

    await userEvent.click(screen.getByRole("button", { name: "New dataset" }));

    await userEvent.click(screen.getByRole("tab", { name: "Upload" }));
    expect(screen.getByRole("button", { name: "upload creates" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "blank creates" })).toBeNull();

    await userEvent.click(screen.getByRole("tab", { name: "Generate" }));
    expect(screen.getByRole("button", { name: "generate creates" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "upload creates" })).toBeNull();

    await userEvent.click(screen.getByRole("tab", { name: "Logfire" }));
    expect(screen.getByRole("button", { name: "logfire creates" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "generate creates" })).toBeNull();
  });

  it.each([
    { tab: "Blank", trigger: "blank creates", path: "/datasets/blank-1" },
    { tab: "Upload", trigger: "upload creates", path: "/datasets/upload-1" },
    { tab: "Generate", trigger: "generate creates", path: "/datasets/gen-1" },
    { tab: "Logfire", trigger: "logfire creates", path: "/datasets/logfire-1" },
  ])("navigates to the new dataset when the $tab tab reports creation", async ({
    tab,
    trigger,
    path,
  }) => {
    renderPage();

    await userEvent.click(screen.getByRole("button", { name: "New dataset" }));
    await userEvent.click(screen.getByRole("tab", { name: tab }));
    await userEvent.click(screen.getByRole("button", { name: trigger }));

    expect(await screen.findByText(`at ${path}`)).toBeTruthy();
  });

  it("tells the user they can create a dataset directly when the list is empty", async () => {
    renderPage();

    expect(await screen.findByText(/create/i)).toBeTruthy();
  });

  it("renders a single level-1 heading titled Datasets", async () => {
    listMock.mockResolvedValue([madeDataset()]);
    renderPage();

    await screen.findByRole("link", { name: "Alpha" });

    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0].textContent).toBe("Datasets");
  });

  it("sums rows and labeled across datasets in the summary strip", async () => {
    listMock.mockResolvedValue([
      madeDataset({ id: "d1", name: "Alpha", row_count: 7, labeled_count: 3 }),
      madeDataset({ id: "d2", name: "Beta", row_count: 13, labeled_count: 9 }),
    ]);
    renderPage();

    await screen.findByRole("link", { name: "Alpha" });

    // Dataset count, total rows, and percent labeled: round(12 / 20) = 60%. The
    // `.stat-value` selector disambiguates the summed figures from the per-row cells,
    // matching the neighbouring DatasetDetail stats test.
    expect(screen.getByText("2", { selector: ".stat-value" })).toBeTruthy();
    expect(screen.getByText("20", { selector: ".stat-value" })).toBeTruthy();
    expect(screen.getByText("60%", { selector: ".stat-value" })).toBeTruthy();

    // The counts ride on the list payload; no per-row stats fetch is made.
    expect(statsMock).not.toHaveBeenCalled();
  });

  it("renders an em dash rather than NaN% when every dataset has zero rows", async () => {
    listMock.mockResolvedValue([
      madeDataset({ id: "d1", name: "Alpha", row_count: 0, labeled_count: 0 }),
      madeDataset({ id: "d2", name: "Beta", row_count: 0, labeled_count: 0 }),
    ]);
    renderPage();

    await screen.findByRole("link", { name: "Alpha" });

    expect(screen.getByText("—", { selector: ".stat-value" })).toBeTruthy();
    expect(screen.queryByText(/NaN/)).toBeNull();
  });

  it("marks a fully labeled dataset complete and shows a fraction otherwise", async () => {
    listMock.mockResolvedValue([
      madeDataset({ id: "d1", name: "Alpha", row_count: 5, labeled_count: 5 }),
      madeDataset({ id: "d2", name: "Beta", row_count: 9, labeled_count: 4 }),
      madeDataset({ id: "d3", name: "Gamma", row_count: 0, labeled_count: 0 }),
    ]);
    renderPage();

    await screen.findByRole("link", { name: "Alpha" });

    // Fully labeled with rows present → the "complete" badge, not a fraction.
    expect(screen.getByText("complete")).toBeTruthy();
    expect(screen.queryByText("5 / 5")).toBeNull();
    // Partially labeled → the plain labeled / row fraction.
    expect(screen.getByText("4 / 9")).toBeTruthy();
    // Zero rows is not "complete" even though labeled_count === row_count.
    expect(screen.getByText("0 / 0")).toBeTruthy();
  });

  it("shows an empty state explaining datasets and offering a create action", async () => {
    listMock.mockResolvedValue([]);
    const { container } = renderPage();

    // The one-sentence explanation: a dataset is rows plus a label space an
    // evaluator is measured against.
    expect(await screen.findByText(/measured against/i)).toBeTruthy();

    const emptyState = container.querySelector(".empty-state");
    expect(emptyState).not.toBeNull();
    expect(within(emptyState as HTMLElement).getByRole("button")).toBeTruthy();
  });

  it("omits the summary strip entirely when there are no datasets", async () => {
    listMock.mockResolvedValue([]);
    const { container } = renderPage();

    await screen.findByText(/measured against/i);

    // No datasets → no summed figures to show, so the strip and its stat cells
    // are absent rather than rendering zeros.
    expect(container.querySelector(".summary-strip")).toBeNull();
    expect(container.querySelector(".stat-value")).toBeNull();
  });

  it("shows each dataset's own row_count in the Rows column", async () => {
    listMock.mockResolvedValue([
      madeDataset({ id: "d1", name: "Alpha", row_count: 7, labeled_count: 3 }),
      madeDataset({ id: "d2", name: "Beta", row_count: 13, labeled_count: 9 }),
    ]);
    const { container } = renderPage();

    await screen.findByRole("link", { name: "Alpha" });

    // The per-row Rows cell reflects that dataset's row_count directly. Table cells
    // carry the raw number; scope to <td> to avoid colliding with the .stat-value
    // summary figures (total rows is 20, distinct from either row's 7 or 13).
    const cellTexts = Array.from(container.querySelectorAll("td")).map((td) => td.textContent);
    expect(cellTexts).toContain("7");
    expect(cellTexts).toContain("13");
  });

  it("rounds percent labeled to a whole number", async () => {
    // 1 of 3 labeled → 33.33…% rounds to 33%.
    listMock.mockResolvedValue([
      madeDataset({ id: "d1", name: "Alpha", row_count: 3, labeled_count: 1 }),
    ]);
    renderPage();

    await screen.findByRole("link", { name: "Alpha" });

    expect(screen.getByText("33%", { selector: ".stat-value" })).toBeTruthy();
    expect(screen.queryByText(/33\.\d/)).toBeNull();
  });

  it("links Open in Logfire to the reference project's evals page when known", async () => {
    setupGet.mockResolvedValue({
      ...EMPTY_SETUP,
      logfire_datasets_url: "https://logfire-us.pydantic.dev/duncan/agent-tracing/evals",
    });
    renderPage();

    const link = await screen.findByRole("link", { name: "Open in Logfire" });
    expect(link).toHaveAttribute(
      "href",
      "https://logfire-us.pydantic.dev/duncan/agent-tracing/evals",
    );
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("omits Open in Logfire when the datasets URL is unknown", async () => {
    renderPage();

    await waitFor(() => expect(listMock).toHaveBeenCalled());
    expect(screen.queryByRole("link", { name: "Open in Logfire" })).toBeNull();
  });
});

function madeLabelSet(overrides: Partial<LabelSetProgress> = {}): LabelSetProgress {
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
    annotated_count: 0,
    row_count: 0,
    ...overrides,
  };
}

describe("DatasetsPage duplicate", () => {
  it("opens the Generate tab seeded from the dataset's primary label set", async () => {
    listMock.mockResolvedValue([madeDataset({ id: "d1", name: "Alpha" })]);
    listLabelSetsMock.mockResolvedValue([madeLabelSet()]);
    renderPage();

    await screen.findByRole("link", { name: "Alpha" });
    await userEvent.click(screen.getByRole("button", { name: "Duplicate Alpha" }));

    expect(await screen.findByRole("tab", { name: "Generate", selected: true })).toBeTruthy();
    await waitFor(() => expect(listLabelSetsMock).toHaveBeenCalledWith("d1"));
    const seed = await screen.findByTestId("generate-seed-schema");
    expect(JSON.parse(seed.textContent ?? "")).toEqual({
      kind: "categorical",
      labels: ["good", "bad"],
      minimum: null,
      maximum: null,
    });
  });

  it("seeds no label schema when the dataset has no label sets", async () => {
    listMock.mockResolvedValue([madeDataset({ id: "d1", name: "Alpha" })]);
    listLabelSetsMock.mockResolvedValue([]);
    renderPage();

    await screen.findByRole("link", { name: "Alpha" });
    await userEvent.click(screen.getByRole("button", { name: "Duplicate Alpha" }));

    await screen.findByRole("tab", { name: "Generate", selected: true });
    await waitFor(() => expect(listLabelSetsMock).toHaveBeenCalledWith("d1"));
    expect(screen.queryByTestId("generate-seed-schema")).toBeNull();
  });
});
