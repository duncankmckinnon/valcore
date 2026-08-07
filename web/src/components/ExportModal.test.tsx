// Tests for the export modal, which fetches a standalone Python script for an evaluator
// version and shows it with a copy-to-clipboard affordance. The redesign moves the Copy
// and Close actions into the modal footer, gives the modal a `size="lg"`, and adds a
// description of what the exported script is. These tests exercise the fetch lifecycle
// (spinner, rendered source, error) and the two footer actions, mocking the API client
// and the clipboard the way the neighbouring component tests mock their collaborators.

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExportModal from "./ExportModal";
import { evaluators } from "../api/client";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    evaluators: { ...actual.evaluators, exportScript: vi.fn() },
  };
});

const exportMock = vi.mocked(evaluators.exportScript);

// A script whose tokens deliberately do NOT overlap the modal's description text, so a
// match on the rendered source can never be satisfied by the description and vice versa.
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

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderModal(overrides: Partial<React.ComponentProps<typeof ExportModal>> = {}) {
  const props = {
    open: true,
    evaluatorId: "e1",
    versionId: "v1",
    onClose: vi.fn(),
    ...overrides,
  };
  render(<ExportModal {...props} />);
  return props;
}

describe("ExportModal", () => {
  it("shows a spinner while the script is loading", () => {
    // A promise that never settles keeps the modal in its loading state.
    exportMock.mockReturnValue(new Promise<string>(() => {}));
    renderModal();

    expect(screen.getByRole("status")).toBeInTheDocument();
    // The source and its actions are withheld until the fetch resolves.
    expect(screen.queryByRole("button", { name: "Copy" })).toBeNull();
  });

  it("renders the fetched script once it resolves", async () => {
    exportMock.mockResolvedValue(SCRIPT);
    renderModal();

    expect(await screen.findByText(/generated-export-marker/)).toBeInTheDocument();
    expect(exportMock).toHaveBeenCalledWith("e1", "v1");
  });

  it("describes the exported script as a standalone program that runs without valcore", async () => {
    exportMock.mockResolvedValue(SCRIPT);
    renderModal();

    // The description explains that this is a standalone script runnable without valcore.
    expect(screen.getByText(/standalone/i)).toBeInTheDocument();
    expect(screen.getByText(/without valcore/i)).toBeInTheDocument();
  });

  it("copies the source to the clipboard and swaps the label to Copied", async () => {
    const user = userEvent.setup();
    stubClipboard();
    exportMock.mockResolvedValue(SCRIPT);
    renderModal();

    await screen.findByText(/generated-export-marker/);
    await user.click(screen.getByRole("button", { name: "Copy" }));

    expect(writeText).toHaveBeenCalledWith(SCRIPT);
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  it("invokes onClose when the footer Close button is pressed", async () => {
    const user = userEvent.setup();
    exportMock.mockResolvedValue(SCRIPT);
    const props = renderModal();

    await screen.findByText(/generated-export-marker/);
    await user.click(screen.getByRole("button", { name: "Close" }));

    expect(props.onClose).toHaveBeenCalled();
  });

  it("surfaces a rejected fetch in the error banner", async () => {
    exportMock.mockRejectedValue(new Error("export failed"));
    renderModal();

    expect(await screen.findByRole("alert")).toHaveTextContent("export failed");
  });
});
