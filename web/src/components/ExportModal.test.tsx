// Tests for the export modal, now a two-level format picker. The modal serves both
// evaluators and datasets via a discriminated `subject` prop. Format is a pair of radios
// (Code / JSON) with Code selected on open; Layout is a Select (Bundled / Split) shown only
// while JSON is active. Code for an evaluator keeps today's single-source-block behaviour and
// the existing Copy button; every other combination renders one named block per emitted file
// with per-file Copy and Download. These tests exercise the fetch lifecycle across format and
// layout changes, the stale-response guard, and the two per-file actions, mocking the API
// client and clipboard the way the neighbouring component tests mock their collaborators.

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExportModal from "./ExportModal";
import { datasets, evaluators } from "../api/client";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    evaluators: { ...actual.evaluators, exportScript: vi.fn(), exportFiles: vi.fn() },
    datasets: { ...actual.datasets, exportFiles: vi.fn() },
  };
});

const exportScript = vi.mocked(evaluators.exportScript);
const evalExportFiles = vi.mocked(evaluators.exportFiles);
const datasetExportFiles = vi.mocked(datasets.exportFiles);

// A script whose tokens deliberately do NOT overlap any description text, so a match on the
// rendered source can never be satisfied by the modal's prose and vice versa.
const SCRIPT = [
  "import argparse",
  "def main():",
  '    return "generated-export-marker"',
].join("\n");

const writeText = vi.fn(() => Promise.resolve());

// userEvent.setup() installs its own clipboard stub, so a test that needs to observe the
// component's write must reinstall this spy *after* setup runs.
function stubClipboard() {
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
    writable: true,
  });
}

// A promise whose resolution is driven by the test, used to interleave two in-flight fetches
// and prove the stale one is discarded.
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

type Subject = React.ComponentProps<typeof ExportModal>["subject"];

const EVALUATOR: Subject = { kind: "evaluator", evaluatorId: "e1", versionId: "v1" };
const DATASET: Subject = { kind: "dataset", datasetId: "d1", versionId: "v9" };

function renderModal(overrides: Partial<React.ComponentProps<typeof ExportModal>> = {}) {
  const props: React.ComponentProps<typeof ExportModal> = {
    open: true,
    onClose: vi.fn(),
    subject: EVALUATOR,
    ...overrides,
  };
  render(<ExportModal {...props} />);
  return props;
}

describe("ExportModal", () => {
  it("shows a spinner while the initial export is loading and withholds Copy", () => {
    // A promise that never settles keeps the modal in its loading state.
    exportScript.mockReturnValue(new Promise<string>(() => {}));
    renderModal();

    expect(screen.getByRole("status")).toBeInTheDocument();
    // Copy is withheld until the fetch resolves, so there is never a live copy action with
    // nothing to write.
    expect(screen.queryByRole("button", { name: "Copy" })).toBeNull();
  });

  it("opens on Code for an evaluator and fetches the script, not the file package", async () => {
    exportScript.mockResolvedValue(SCRIPT);
    renderModal();

    // Code is the default selection for both subject kinds.
    expect(screen.getByRole("radio", { name: "Code" })).toBeChecked();

    expect(await screen.findByText(/generated-export-marker/)).toBeInTheDocument();
    expect(exportScript).toHaveBeenCalledWith("e1", "v1");
    expect(evalExportFiles).not.toHaveBeenCalled();

    // Evaluator Code keeps the single-block layout: one Copy, no filename, no Download.
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download" })).toBeNull();
  });

  it("describes evaluator Code as a standalone program that runs without valcore", async () => {
    exportScript.mockResolvedValue(SCRIPT);
    renderModal();

    expect(screen.getByText(/standalone/i)).toBeInTheDocument();
    expect(screen.getByText(/without valcore/i)).toBeInTheDocument();
  });

  it("switching to JSON fetches the bundled package and renders one named block per file", async () => {
    const user = userEvent.setup();
    exportScript.mockResolvedValue(SCRIPT);
    evalExportFiles.mockResolvedValue({
      "refusal_quality.json": '{"kind":"valcore/eval-package"}',
    });
    renderModal();

    await screen.findByText(/generated-export-marker/);
    await user.click(screen.getByRole("radio", { name: "JSON" }));

    expect(evalExportFiles).toHaveBeenCalledWith("v1", "json", "bundled");
    // The returned map key is shown as the file's name label above its block.
    expect(await screen.findByText("refusal_quality.json")).toBeInTheDocument();
  });

  it("reflects the JSON format in the description, naming valcore_judge.py to run it", async () => {
    const user = userEvent.setup();
    exportScript.mockResolvedValue(SCRIPT);
    evalExportFiles.mockResolvedValue({ "refusal_quality.json": "{}" });
    renderModal();

    await screen.findByText(/generated-export-marker/);
    await user.click(screen.getByRole("radio", { name: "JSON" }));

    expect(await screen.findByText(/valcore_judge\.py/)).toBeInTheDocument();
    expect(screen.getByText(/pydantic-ai/i)).toBeInTheDocument();
  });

  it("choosing Split refetches the package with the split layout", async () => {
    const user = userEvent.setup();
    exportScript.mockResolvedValue(SCRIPT);
    evalExportFiles.mockResolvedValue({ "refusal_quality.json": "{}" });
    renderModal();

    await screen.findByText(/generated-export-marker/);
    await user.click(screen.getByRole("radio", { name: "JSON" }));
    await screen.findByText("refusal_quality.json");

    // The layout Select only exists while JSON is active.
    await user.selectOptions(
      screen.getByRole("combobox"),
      screen.getByRole("option", { name: "Split" }),
    );

    expect(evalExportFiles).toHaveBeenCalledWith("v1", "json", "split");
  });

  it("opens a dataset on Code and fetches its module via datasets.exportFiles", async () => {
    datasetExportFiles.mockResolvedValue({ "refusal_quality.py": "# dataset module" });
    renderModal({ subject: DATASET });

    expect(screen.getByRole("radio", { name: "Code" })).toBeChecked();

    expect(await screen.findByText("refusal_quality.py")).toBeInTheDocument();
    expect(datasetExportFiles).toHaveBeenCalledWith(
      "d1",
      "code",
      expect.objectContaining({ layout: "bundled" }),
    );
    expect(exportScript).not.toHaveBeenCalled();
    // The file-package layout carries a Download action alongside Copy.
    expect(screen.getByRole("button", { name: "Download" })).toBeInTheDocument();
  });

  it("describes dataset Code as a pydantic_evals.Dataset module", async () => {
    datasetExportFiles.mockResolvedValue({ "refusal_quality.py": "# dataset module" });
    renderModal({ subject: DATASET });

    expect(await screen.findByText(/pydantic_evals\.Dataset/i)).toBeInTheDocument();
  });

  it("pretty-prints JSON file bodies for display", async () => {
    const user = userEvent.setup();
    exportScript.mockResolvedValue(SCRIPT);
    // Compact on the wire; the space after the colon can only appear once pretty-printed.
    evalExportFiles.mockResolvedValue({ "refusal_quality.json": '{"score":"refusal"}' });
    renderModal();

    await screen.findByText(/generated-export-marker/);
    await user.click(screen.getByRole("radio", { name: "JSON" }));

    expect(await screen.findByText(/"score": "refusal"/)).toBeInTheDocument();
  });

  it("renders a non-JSON body raw instead of throwing", async () => {
    const user = userEvent.setup();
    exportScript.mockResolvedValue(SCRIPT);
    // A body that JSON.parse cannot handle must fall back to the raw text.
    evalExportFiles.mockResolvedValue({ "broken.json": "not json at all {oops" });
    renderModal();

    await screen.findByText(/generated-export-marker/);
    await user.click(screen.getByRole("radio", { name: "JSON" }));

    expect(await screen.findByText(/not json at all \{oops/)).toBeInTheDocument();
  });

  it("copies the matching file's text when several files are shown", async () => {
    const user = userEvent.setup();
    stubClipboard();
    exportScript.mockResolvedValue(SCRIPT);
    evalExportFiles.mockResolvedValue({
      "pkg.agent.json": "agent-file-body",
      "pkg.dataset.json": "dataset-file-body",
    });
    renderModal();

    await screen.findByText(/generated-export-marker/);
    await user.click(screen.getByRole("radio", { name: "JSON" }));
    await screen.findByText("pkg.dataset.json");

    // Files render in map order, so the second Copy belongs to the second file.
    const copyButtons = screen.getAllByRole("button", { name: "Copy" });
    await user.click(copyButtons[1]);

    expect(writeText).toHaveBeenCalledWith("dataset-file-body");
  });

  it("downloads a file named by its map key and revokes the object URL", async () => {
    const user = userEvent.setup();
    exportScript.mockResolvedValue(SCRIPT);
    evalExportFiles.mockResolvedValue({
      "pkg.agent.json": "agent-file-body",
      "pkg.dataset.json": "dataset-file-body",
    });

    // jsdom does not implement the object-URL API; stub it so the component can synthesize a
    // download anchor and clean up after itself.
    const createObjectURL = vi.fn(() => "blob:mock-url");
    const revokeObjectURL = vi.fn();
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;

    // Capture the synthesized anchor and neutralize its click so no real navigation happens.
    const clickSpy = vi.fn();
    const realCreate = document.createElement.bind(document);
    let anchor: HTMLAnchorElement | null = null;
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = realCreate(tag) as HTMLElement;
      if (tag === "a") {
        anchor = el as HTMLAnchorElement;
        (el as HTMLAnchorElement).click = clickSpy;
      }
      return el;
    });

    renderModal();

    await screen.findByText(/generated-export-marker/);
    await user.click(screen.getByRole("radio", { name: "JSON" }));
    await screen.findByText("pkg.dataset.json");

    const downloadButtons = screen.getAllByRole("button", { name: "Download" });
    await user.click(downloadButtons[1]);

    expect(anchor).not.toBeNull();
    expect(anchor!.download).toBe("pkg.dataset.json");
    expect(clickSpy).toHaveBeenCalled();
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
  });

  it("surfaces a rejected fetch in the error banner and shows no output block", async () => {
    exportScript.mockRejectedValue(new Error("export failed"));
    renderModal();

    expect(await screen.findByRole("alert")).toHaveTextContent("export failed");
    // The source text never renders when the fetch fails.
    expect(screen.queryByText(/generated-export-marker/)).toBeNull();
  });

  it("keeps the later response when an earlier in-flight fetch resolves last", async () => {
    const user = userEvent.setup();
    exportScript.mockResolvedValue(SCRIPT);

    // The bundled fetch is deliberately slow; the split fetch that supersedes it resolves
    // first. The stale bundled resolution must not overwrite the split result.
    const bundled = deferred<Record<string, string>>();
    const split = deferred<Record<string, string>>();
    evalExportFiles.mockReturnValueOnce(bundled.promise).mockReturnValueOnce(split.promise);

    renderModal();
    await screen.findByText(/generated-export-marker/);

    await user.click(screen.getByRole("radio", { name: "JSON" }));
    await user.selectOptions(
      screen.getByRole("combobox"),
      screen.getByRole("option", { name: "Split" }),
    );

    // The newer (split) request settles first, then the stale (bundled) one.
    split.resolve({ "split-file.json": "{}" });
    expect(await screen.findByText("split-file.json")).toBeInTheDocument();

    bundled.resolve({ "bundled-file.json": "{}" });
    // Give the stale resolution a chance to (wrongly) win before asserting it did not.
    await Promise.resolve();

    expect(screen.getByText("split-file.json")).toBeInTheDocument();
    expect(screen.queryByText("bundled-file.json")).toBeNull();
  });

  it("invokes onClose when the footer Close button is pressed", async () => {
    const user = userEvent.setup();
    exportScript.mockResolvedValue(SCRIPT);
    const props = renderModal();

    await screen.findByText(/generated-export-marker/);
    await user.click(screen.getByRole("button", { name: "Close" }));

    expect(props.onClose).toHaveBeenCalled();
  });

  it("toggling JSON then back to Code restores the script block and hides the layout Select", async () => {
    const user = userEvent.setup();
    exportScript.mockResolvedValue(SCRIPT);
    evalExportFiles.mockResolvedValue({ "refusal_quality.json": "{}" });
    renderModal();

    await screen.findByText(/generated-export-marker/);
    // The layout Select is absent while Code is active.
    expect(screen.queryByRole("combobox")).toBeNull();

    await user.click(screen.getByRole("radio", { name: "JSON" }));
    await screen.findByText("refusal_quality.json");
    // The layout Select appears only once JSON is active.
    expect(screen.getByRole("combobox")).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: "Code" }));

    // Returning to Code refires the standalone-script fetch and drops the file-package chrome.
    expect(await screen.findByText(/generated-export-marker/)).toBeInTheDocument();
    expect(exportScript).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("refusal_quality.json")).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Download" })).toBeNull();
  });
});
