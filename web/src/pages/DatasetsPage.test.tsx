import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import DatasetsPage from "./DatasetsPage";
import { datasets } from "../api/client";

// The three creation paths are owned by other tasks; stub each so it reports a
// distinct id back through its `onCreated` prop. Blank and Generate hand back a
// dataset id string; Upload hands back a `DatasetCreated` envelope.
vi.mock("../components/DatasetBlankForm", () => ({
  default: ({ onCreated }: { onCreated: (id: string) => void }) => (
    <button onClick={() => onCreated("blank-1")}>blank creates</button>
  ),
}));
vi.mock("../components/DatasetGenerateForm", () => ({
  default: ({ onCreated }: { onCreated: (id: string) => void }) => (
    <button onClick={() => onCreated("gen-1")}>generate creates</button>
  ),
}));
vi.mock("../components/DatasetUpload", () => ({
  default: ({ onCreated }: { onCreated: (created: { dataset: { id: string } }) => void }) => (
    <button onClick={() => onCreated({ dataset: { id: "upload-1" } } as never)}>upload creates</button>
  ),
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    datasets: { ...actual.datasets, list: vi.fn(), stats: vi.fn() },
  };
});

const listMock = vi.mocked(datasets.list);
const statsMock = vi.mocked(datasets.stats);

// A sentinel route so navigation to /datasets/:id is observable as text.
function LocationProbe() {
  const location = useLocation();
  return <p>at {location.pathname}</p>;
}

function renderPage() {
  render(
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
  });

  it.each([
    { tab: "Blank", trigger: "blank creates", path: "/datasets/blank-1" },
    { tab: "Upload", trigger: "upload creates", path: "/datasets/upload-1" },
    { tab: "Generate", trigger: "generate creates", path: "/datasets/gen-1" },
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
});
