import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DatasetBlankForm from "./DatasetBlankForm";
import { datasets } from "../api/client";
import type { Dataset } from "../api/types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    datasets: { ...actual.datasets, create: vi.fn() },
  };
});

const createMock = vi.mocked(datasets.create);

function madeDataset(): Dataset {
  return {
    id: "d1",
    created_at: "2026-01-01T00:00:00Z",
    name: "My set",
    description: "",
    columns: ["question", "answer"],
    label_schema: { kind: "categorical", labels: [], minimum: null, maximum: null },
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DatasetBlankForm", () => {
  it("keeps submit disabled until a name and at least one column are present", async () => {
    render(<DatasetBlankForm onCreated={vi.fn()} />);

    const submit = screen.getByRole("button", { name: "Create" });
    expect(submit).toBeDisabled();

    await userEvent.type(screen.getByLabelText("Name"), "My set");
    expect(submit).toBeDisabled();

    await userEvent.type(screen.getByLabelText("Columns (comma separated)"), "question, answer");
    expect(submit).not.toBeDisabled();
  });

  it("does not enable submit for a blank column string", async () => {
    render(<DatasetBlankForm onCreated={vi.fn()} />);

    await userEvent.type(screen.getByLabelText("Name"), "My set");
    await userEvent.type(screen.getByLabelText("Columns (comma separated)"), "   ,  ");

    expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
  });

  it("creates the dataset with parsed columns and reports the new id", async () => {
    createMock.mockResolvedValue(madeDataset());
    const onCreated = vi.fn();
    render(<DatasetBlankForm onCreated={onCreated} />);

    await userEvent.type(screen.getByLabelText("Name"), "My set");
    await userEvent.type(screen.getByLabelText("Columns (comma separated)"), "question, answer");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(createMock).toHaveBeenCalledOnce());
    expect(createMock).toHaveBeenCalledWith(
      expect.objectContaining({ name: "My set", columns: ["question", "answer"] }),
    );
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("d1"));
  });
});
