import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import EvaluatorDetail from "./EvaluatorDetail";
import { ApiError, api, evaluators } from "../api/client";
import type { EvaluatorVersion } from "../api/types";

const navigate = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

// The page-level tests exercise EvaluatorDetail's own wiring, so VersionEditor is
// stubbed to a marker that reports whether it received a draft (null) or a version,
// and to which evaluator id it was bound.
vi.mock("../components/VersionEditor", () => ({
  VersionEditor: ({
    version,
    evaluatorId,
    onSaved,
  }: {
    version: EvaluatorVersion | null;
    evaluatorId: string;
    onSaved?: (version: EvaluatorVersion) => void;
  }) => (
    <div>
      <p>{version === null ? "Draft editor" : `Editing version ${version.id}`}</p>
      <p>editor-evaluator-id: {evaluatorId}</p>
      <button type="button" onClick={() => onSaved?.({ id: "v-new" } as EvaluatorVersion)}>
        simulate save
      </button>
    </div>
  ),
}));

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: vi.fn(),
    evaluators: {
      get: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
      deleteVersion: vi.fn(),
      createVersion: vi.fn(),
    },
  };
});

const config = { models: ["model-a"], tools: [], capabilities: [] };

function makeVersion(overrides: Partial<EvaluatorVersion> = {}): EvaluatorVersion {
  return {
    id: "v1",
    created_at: "2026-07-01T00:00:00Z",
    evaluator_id: "e1",
    version_name: "v1",
    notes: "",
    frozen: false,
    model: "model-a",
    instructions: "Judge it.",
    prompt_template: "{answer}",
    required_columns: ["answer"],
    output_fields: [],
    score_field: "verdict",
    score_kind: "categorical",
    score_labels: ["pass", "fail"],
    score_minimum: null,
    score_maximum: null,
    capabilities: [],
    tools: [],
    ...overrides,
  };
}

function makeDetail(overrides: Partial<{
  id: string;
  name: string;
  description: string;
  active_version_id: string | null;
  versions: EvaluatorVersion[];
}> = {}) {
  return {
    id: "e1",
    created_at: "2026-07-01T00:00:00Z",
    name: "My Eval",
    description: "Checks answers.",
    active_version_id: "v1",
    versions: [makeVersion({ id: "v1" })],
    ...overrides,
  } as unknown as import("../api/types").Evaluator;
}

function renderDetail(id = "e1") {
  return render(
    <MemoryRouter initialEntries={[`/evaluators/${id}`]}>
      <EvaluatorDetail id={id} />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("EvaluatorDetail: draft editor", () => {
  it("renders the draft editor for an evaluator with no versions, not 'No versions yet.'", async () => {
    vi.mocked(api).mockResolvedValue(config);
    vi.mocked(evaluators.get).mockResolvedValue(
      makeDetail({ active_version_id: null, versions: [] }),
    );
    renderDetail();

    expect(await screen.findByText("Draft editor")).toBeTruthy();
    expect(screen.queryByText("No versions yet.")).toBeNull();
    expect(screen.getByText("editor-evaluator-id: e1")).toBeTruthy();
  });

  it("New version shows the draft editor while keeping existing versions listed", async () => {
    vi.mocked(api).mockResolvedValue(config);
    vi.mocked(evaluators.get).mockResolvedValue(
      makeDetail({
        active_version_id: "v1",
        versions: [makeVersion({ id: "v1" }), makeVersion({ id: "v2", version_name: "v2" })],
      }),
    );
    const user = userEvent.setup();
    renderDetail();

    expect(await screen.findByText("Editing version v1")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "New version" }));

    expect(screen.getByText("Draft editor")).toBeTruthy();
    const versionSelect = screen.getByRole("combobox", { name: "Version" });
    expect(within(versionSelect).getAllByRole("option")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Export" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
  });

  it("selects the newly created version after a draft is saved", async () => {
    vi.mocked(api).mockResolvedValue(config);
    vi.mocked(evaluators.get)
      .mockResolvedValueOnce(makeDetail({ active_version_id: null, versions: [] }))
      .mockResolvedValueOnce(
        makeDetail({ active_version_id: null, versions: [makeVersion({ id: "v-new" })] }),
      );
    const user = userEvent.setup();
    renderDetail();

    await screen.findByText("Draft editor");
    await user.click(screen.getByRole("button", { name: "simulate save" }));

    expect(await screen.findByText("Editing version v-new")).toBeTruthy();
    expect(evaluators.get).toHaveBeenCalledTimes(2);
  });
});

describe("EvaluatorDetail: delete version", () => {
  it("confirms then calls deleteVersion and reloads onto the newest survivor", async () => {
    vi.mocked(api).mockResolvedValue(config);
    vi.mocked(evaluators.get)
      .mockResolvedValueOnce(
        makeDetail({
          active_version_id: "v1",
          versions: [
            makeVersion({ id: "v1", created_at: "2026-07-01T00:00:00Z" }),
            makeVersion({ id: "v2", version_name: "v2", created_at: "2026-07-02T00:00:00Z" }),
            makeVersion({ id: "v3", version_name: "v3", created_at: "2026-07-03T00:00:00Z" }),
          ],
        }),
      )
      // The API lists versions oldest-first, so the reload returns v2 before v3. The page
      // must still land on v3, the newest survivor, rather than versions[0].
      .mockResolvedValueOnce(
        makeDetail({
          active_version_id: "v1",
          versions: [
            makeVersion({ id: "v2", version_name: "v2", created_at: "2026-07-02T00:00:00Z" }),
            makeVersion({ id: "v3", version_name: "v3", created_at: "2026-07-03T00:00:00Z" }),
          ],
        }),
      );
    vi.mocked(evaluators.deleteVersion).mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderDetail();

    await screen.findByText("Editing version v1");
    await user.click(screen.getByRole("button", { name: "Delete version" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(evaluators.deleteVersion).toHaveBeenCalledWith("e1", "v1"),
    );
    expect(evaluators.get).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("Editing version v3")).toBeTruthy();
  });

  it("keeps the dialog open and shows the run count when deleteVersion is referenced", async () => {
    vi.mocked(api).mockResolvedValue(config);
    vi.mocked(evaluators.get).mockResolvedValue(
      makeDetail({ active_version_id: "v1", versions: [makeVersion({ id: "v1" })] }),
    );
    vi.mocked(evaluators.deleteVersion).mockRejectedValue(
      new ApiError("cannot delete", "ReferencedError", 409, { run_count: 3 }),
    );
    const user = userEvent.setup();
    renderDetail();

    await screen.findByText("Editing version v1");
    await user.click(screen.getByRole("button", { name: "Delete version" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));

    expect(await screen.findByText("3 runs depend on this. Delete them first.")).toBeTruthy();
    // The confirm button is still present, so the dialog did not close, and no reload ran.
    expect(screen.getByRole("button", { name: "Delete" })).toBeTruthy();
    expect(evaluators.get).toHaveBeenCalledTimes(1);
  });
});

describe("EvaluatorDetail: rename and delete evaluator", () => {
  it("calls evaluators.update when the title is edited", async () => {
    vi.mocked(api).mockResolvedValue(config);
    vi.mocked(evaluators.get).mockResolvedValue(makeDetail({ name: "My Eval" }));
    vi.mocked(evaluators.update).mockResolvedValue(makeDetail({ name: "Renamed" }));
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("heading", { name: "My Eval" }));
    const input = screen.getByLabelText("Evaluator name");
    await user.clear(input);
    await user.type(input, "Renamed");
    await user.tab();

    await waitFor(() =>
      expect(evaluators.update).toHaveBeenCalledWith(
        "e1",
        expect.objectContaining({ name: "Renamed" }),
      ),
    );
  });

  it("does not call evaluators.update when the title is cleared to empty", async () => {
    vi.mocked(api).mockResolvedValue(config);
    vi.mocked(evaluators.get).mockResolvedValue(makeDetail({ name: "My Eval" }));
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("heading", { name: "My Eval" }));
    const input = screen.getByLabelText("Evaluator name");
    await user.clear(input);
    await user.tab();

    expect(evaluators.update).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "My Eval" })).toBeTruthy();
  });

  it("confirms then calls remove and navigates back to the list", async () => {
    vi.mocked(api).mockResolvedValue(config);
    vi.mocked(evaluators.get).mockResolvedValue(makeDetail());
    vi.mocked(evaluators.remove).mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderDetail();

    await screen.findByText("Editing version v1");
    await user.click(screen.getByRole("button", { name: "Delete evaluator" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(evaluators.remove).toHaveBeenCalledWith("e1"));
    expect(navigate).toHaveBeenCalledWith("/evaluators");
  });
});
