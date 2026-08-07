import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DatasetUpload from "./DatasetUpload";
import { datasets } from "../api/client";

vi.mock("../api/client", () => ({
  datasets: {
    upload: vi.fn(),
  },
}));

const uploadMock = vi.mocked(datasets.upload);

const CSV = "text,label\nhello,good\nworld,bad\n";

function csvFile(): File {
  return new File([CSV], "data.csv", { type: "text/csv" });
}

function columnHeaders(): string[] {
  const table = screen.getByRole("table");
  return within(table)
    .getAllByRole("columnheader")
    .map((th) => th.textContent ?? "");
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DatasetUpload", () => {
  it("parses a CSV into a preview with inferred columns", async () => {
    render(<DatasetUpload onCreated={vi.fn()} />);

    await userEvent.upload(screen.getByLabelText("File (CSV or JSONL)"), csvFile());

    await waitFor(() => expect(columnHeaders()).toEqual(["text", "label"]));
    const table = screen.getByRole("table");
    expect(within(table).getByText("hello")).toBeTruthy();
    expect(within(table).getByText("world")).toBeTruthy();
  });

  it("excludes the chosen label column from the data columns", async () => {
    render(<DatasetUpload onCreated={vi.fn()} />);

    await userEvent.upload(screen.getByLabelText("File (CSV or JSONL)"), csvFile());
    await waitFor(() => expect(columnHeaders()).toEqual(["text", "label"]));

    await userEvent.selectOptions(screen.getByLabelText("Label column"), "label");

    await waitFor(() => expect(columnHeaders()).toEqual(["text"]));
  });

  it("posts the file to the upload endpoint on submit", async () => {
    uploadMock.mockResolvedValue({
      dataset: {
        id: "d1",
        created_at: "2026-01-01T00:00:00Z",
        name: "data",
        description: "",
        columns: ["text"],
        label_schema: { kind: "categorical", labels: [], minimum: null, maximum: null },
        row_count: 2,
        labeled_count: 0,
      },
      row_count: 2,
    });
    const onCreated = vi.fn();
    render(<DatasetUpload onCreated={onCreated} />);

    await userEvent.upload(screen.getByLabelText("File (CSV or JSONL)"), csvFile());
    await waitFor(() => expect(columnHeaders()).toEqual(["text", "label"]));

    await userEvent.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() => expect(uploadMock).toHaveBeenCalledOnce());
    const form = uploadMock.mock.calls[0][0] as FormData;
    const uploaded = form.get("file") as File;
    expect(uploaded.name).toBe("data.csv");
    expect(form.get("name")).toBe("data");
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
  });
});

// -- Redesigned modal chrome -------------------------------------------------
// The redesign adds a one-line description of the accepted file shape and a tooltip on
// the label-column field explaining what a label column is.

describe("DatasetUpload chrome", () => {
  it("describes the accepted file shape", () => {
    render(<DatasetUpload onCreated={vi.fn()} />);

    // The field label already names the formats once; the added description names them a
    // second time, so more than one node mentions the format.
    expect(screen.getAllByText(/JSONL/i).length).toBeGreaterThan(1);
  });

  it("explains the label column via a tooltip once a file is chosen", async () => {
    render(<DatasetUpload onCreated={vi.fn()} />);

    await userEvent.upload(screen.getByLabelText("File (CSV or JSONL)"), csvFile());
    await waitFor(() => expect(columnHeaders()).toEqual(["text", "label"]));

    await userEvent.click(screen.getByRole("button", { name: /more information/i }));

    expect(screen.getByRole("tooltip").textContent).toMatch(/label/i);
  });
});
