import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DatasetSettingsModal from "./DatasetSettingsModal";
import { ApiError, datasets } from "../api/client";
import type { Dataset } from "../api/types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    datasets: { ...actual.datasets, update: vi.fn() },
  };
});

const updateMock = vi.mocked(datasets.update);

function madeDataset(): Dataset {
  return {
    id: "d1",
    created_at: "2026-01-01T00:00:00Z",
    name: "My set",
    description: "desc",
    columns: ["question", "answer"],
  };
}

function renderModal(overrides: Partial<React.ComponentProps<typeof DatasetSettingsModal>> = {}) {
  const props = {
    open: true,
    dataset: madeDataset(),
    onSaved: vi.fn(),
    onClose: vi.fn(),
    ...overrides,
  };
  render(<DatasetSettingsModal {...props} />);
  return props;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DatasetSettingsModal", () => {
  it("submits a rename as column_renames plus the final column list", async () => {
    updateMock.mockResolvedValue(madeDataset());
    renderModal();

    const first = screen.getByLabelText("Column 1");
    await userEvent.clear(first);
    await userEvent.type(first, "query");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(updateMock).toHaveBeenCalledOnce());
    const [id, body] = updateMock.mock.calls[0];
    expect(id).toBe("d1");
    expect(body).toEqual({ columns: ["query", "answer"], column_renames: { question: "query" } });
  });

  it("adds a column to the list without a rename entry", async () => {
    updateMock.mockResolvedValue(madeDataset());
    renderModal();

    await userEvent.click(screen.getByRole("button", { name: "Add column" }));
    await userEvent.type(screen.getByLabelText("Column 3"), "context");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(updateMock).toHaveBeenCalledOnce());
    const [, body] = updateMock.mock.calls[0];
    expect(body.columns).toEqual(["question", "answer", "context"]);
    expect(body.column_renames?.context).toBeUndefined();
  });

  it("drops a removed column from the list", async () => {
    updateMock.mockResolvedValue(madeDataset());
    renderModal();

    await userEvent.click(screen.getByRole("button", { name: "Remove column 2" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(updateMock).toHaveBeenCalledOnce());
    const [, body] = updateMock.mock.calls[0];
    expect(body.columns).toEqual(["question"]);
  });

  it("blocks submit and warns when a rename collides with an existing name", async () => {
    renderModal();

    const second = screen.getByLabelText("Column 2");
    await userEvent.clear(second);
    await userEvent.type(second, "question");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(screen.getByText(/duplicate/i)).toBeTruthy();
    expect(updateMock).not.toHaveBeenCalled();
  });

  it("routes a server error through the banner and keeps the modal open", async () => {
    updateMock.mockRejectedValue(new ApiError("bad config", "ConfigError", 422));
    const props = renderModal();

    const first = screen.getByLabelText("Column 1");
    await userEvent.clear(first);
    await userEvent.type(first, "query");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("bad config");
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it("sends an empty patch and closes when nothing changed", async () => {
    updateMock.mockResolvedValue(madeDataset());
    const props = renderModal();

    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(updateMock).toHaveBeenCalledOnce());
    const [id, body] = updateMock.mock.calls[0];
    expect(id).toBe("d1");
    expect(body).toEqual({});
    await waitFor(() => expect(props.onClose).toHaveBeenCalled());
    expect(props.onSaved).toHaveBeenCalledWith(madeDataset());
  });

  it("treats an emptied column with an original name as a removal, not a rename", async () => {
    updateMock.mockResolvedValue(madeDataset());
    renderModal();

    const first = screen.getByLabelText("Column 1");
    await userEvent.clear(first);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(updateMock).toHaveBeenCalledOnce());
    const [, body] = updateMock.mock.calls[0];
    expect(body.columns).toEqual(["answer"]);
    expect(body.column_renames).toBeUndefined();
  });
});

// -- Modal chrome -------------------------------------------------------------

describe("DatasetSettingsModal chrome", () => {
  it("describes that this only renames the dataset and reshapes its columns", () => {
    renderModal();

    expect(screen.getByText(/rename this dataset or reshape its columns/i)).toBeInTheDocument();
  });

  it("keeps the Cancel action wired to onClose from the footer", async () => {
    const props = renderModal();

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(props.onClose).toHaveBeenCalled();
  });
});
