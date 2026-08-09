import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import EvaluatorsPage from "./EvaluatorsPage";
import { api, evaluators } from "../api/client";
import { GATEWAY_BLOCKER, useSetup } from "../components/useSetup";
import type { UseSetupResult } from "../components/useSetup";
import type { Evaluator, GeneratedConfig } from "../api/types";

const navigate = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: vi.fn(),
    evaluators: {
      list: vi.fn(),
      create: vi.fn(),
      createVersion: vi.fn(),
      generate: vi.fn(),
    },
  };
});

// Only Generate (criteria mode) calls the model through the gateway; scratch-mode
// Create must stay usable regardless. The hook is mocked directly so each test can set
// gatewayReady without re-exercising useSetup's own fetch machinery.
vi.mock("../components/useSetup", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../components/useSetup")>();
  return { ...actual, useSetup: vi.fn() };
});

const useSetupMock = vi.mocked(useSetup);

function makeSetupResult(overrides: Partial<UseSetupResult> = {}): UseSetupResult {
  return {
    status: null,
    gatewayReady: true,
    loading: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  };
}

const config = { models: ["model-a", "model-b"], tools: [], capabilities: [] };

function makeEvaluator(overrides: Partial<Evaluator> = {}): Evaluator {
  return {
    id: "new-eval",
    created_at: "2026-08-01T00:00:00Z",
    name: "Fresh",
    description: "",
    active_version_id: null,
    ...overrides,
  };
}

function makeDraft(): GeneratedConfig {
  return {
    name: "generated",
    version_name: "v1",
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
    rationale: "because",
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/evaluators"]}>
      <EvaluatorsPage />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

beforeEach(() => {
  useSetupMock.mockReturnValue(makeSetupResult());
});

describe("EvaluatorsPage: new-evaluator modal", () => {
  it("defaults to From scratch and shows name + description, not criteria", async () => {
    vi.mocked(evaluators.list).mockResolvedValue([]);
    vi.mocked(api).mockResolvedValue(config);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "New evaluator" }));

    expect(screen.getByRole("tablist")).toBeTruthy();
    // The selected tab exposes aria-selected="true", which testing-library reads
    // through the `selected` option rather than a jest-dom attribute matcher.
    expect(screen.getByRole("tab", { name: "From scratch", selected: true })).toBeTruthy();

    expect(screen.getByLabelText("Evaluator name")).toBeTruthy();
    expect(screen.getByLabelText("Description")).toBeTruthy();
    expect(screen.queryByLabelText("Criteria")).toBeNull();
  });

  it("submitting in scratch mode calls create and not generate or createVersion", async () => {
    vi.mocked(evaluators.list).mockResolvedValue([]);
    vi.mocked(api).mockResolvedValue(config);
    vi.mocked(evaluators.create).mockResolvedValue(makeEvaluator({ id: "e-scratch" }));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "New evaluator" }));
    await user.type(screen.getByLabelText("Evaluator name"), "My evaluator");
    await user.type(screen.getByLabelText("Description"), "A hand-written one");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(evaluators.create).toHaveBeenCalledWith(
        expect.objectContaining({ name: "My evaluator", description: "A hand-written one" }),
      ),
    );
    expect(evaluators.generate).not.toHaveBeenCalled();
    expect(evaluators.createVersion).not.toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith("/evaluators/e-scratch");
  });

  it("switching to From criteria and submitting calls generate, create, then createVersion", async () => {
    vi.mocked(evaluators.list).mockResolvedValue([]);
    vi.mocked(api).mockResolvedValue(config);
    vi.mocked(evaluators.generate).mockResolvedValue(makeDraft());
    vi.mocked(evaluators.create).mockResolvedValue(makeEvaluator({ id: "e-gen" }));
    vi.mocked(evaluators.createVersion).mockResolvedValue({} as never);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "New evaluator" }));
    await user.click(screen.getByRole("tab", { name: "From criteria" }));

    await user.type(screen.getByLabelText("Evaluator name"), "Criteria eval");
    await user.type(screen.getByLabelText("Criteria"), "A good answer is concise.");
    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(evaluators.createVersion).toHaveBeenCalledOnce());
    expect(evaluators.generate).toHaveBeenCalledWith(
      expect.objectContaining({ criteria: "A good answer is concise." }),
    );
    expect(evaluators.create).toHaveBeenCalledWith(
      expect.objectContaining({ name: "Criteria eval" }),
    );

    const generateOrder = vi.mocked(evaluators.generate).mock.invocationCallOrder[0];
    const createOrder = vi.mocked(evaluators.create).mock.invocationCallOrder[0];
    const versionOrder = vi.mocked(evaluators.createVersion).mock.invocationCallOrder[0];
    expect(generateOrder).toBeLessThan(createOrder);
    expect(createOrder).toBeLessThan(versionOrder);
    expect(navigate).toHaveBeenCalledWith("/evaluators/e-gen");
  });

  it("disables submit with an empty name in both modes", async () => {
    vi.mocked(evaluators.list).mockResolvedValue([]);
    vi.mocked(api).mockResolvedValue(config);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "New evaluator" }));

    // Scratch mode: no name -> Create disabled.
    expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();

    // Criteria mode: no name -> Generate disabled even with criteria filled.
    await user.click(screen.getByRole("tab", { name: "From criteria" }));
    await user.type(screen.getByLabelText("Criteria"), "Some criteria.");
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
  });
});

describe("EvaluatorsPage: page chrome", () => {
  it("renders a single level-1 heading titled 'Evaluators'", async () => {
    vi.mocked(evaluators.list).mockResolvedValue([]);
    vi.mocked(api).mockResolvedValue(config);
    renderPage();

    // PageHeader owns the one and only <h1>; the page must not mint a second one.
    expect(await screen.findByRole("heading", { level: 1, name: "Evaluators" })).toBeTruthy();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("shows an explanatory empty state with its own create action when there are no evaluators", async () => {
    vi.mocked(evaluators.list).mockResolvedValue([]);
    vi.mocked(api).mockResolvedValue(config);
    const user = userEvent.setup();
    renderPage();

    // The bare "No evaluators yet." string is replaced by an explanation of what an
    // evaluator is: a prompt plus an output contract that grades rows.
    expect(await screen.findByText(/output contract/i)).toBeTruthy();
    expect(screen.queryByText("No evaluators yet.")).toBeNull();

    // The empty state carries its own call to action, distinct from the header's
    // "New evaluator" button, and it opens the same create modal.
    await user.click(screen.getByRole("button", { name: "Create evaluator" }));
    expect(screen.getByRole("tablist")).toBeTruthy();
  });
});

describe("EvaluatorsPage: create modal guidance", () => {
  async function openModal() {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "New evaluator" }));
    return user;
  }

  it("blocks Create with an empty name and explains 'Add a name'", async () => {
    vi.mocked(evaluators.list).mockResolvedValue([]);
    vi.mocked(api).mockResolvedValue(config);
    await openModal();

    expect(screen.getByText("Add a name")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
  });

  it("in criteria mode with a name but no criteria explains 'Describe the criteria'", async () => {
    vi.mocked(evaluators.list).mockResolvedValue([]);
    vi.mocked(api).mockResolvedValue(config);
    const user = await openModal();

    await user.click(screen.getByRole("tab", { name: "From criteria" }));
    await user.type(screen.getByLabelText("Evaluator name"), "Concise judge");

    // With the name filled the "Add a name" blocker clears and the criteria blocker
    // takes its place; the primary action stays disabled.
    expect(screen.queryByText("Add a name")).toBeNull();
    expect(screen.getByText("Describe the criteria")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
  });

  it("shows the three-step generation explanation only in criteria mode", async () => {
    vi.mocked(evaluators.list).mockResolvedValue([]);
    vi.mocked(api).mockResolvedValue(config);
    const user = await openModal();

    // Scratch mode explains you author the first version yourself, and shows none of
    // the criteria-only guidance about generation.
    expect(screen.getByText(/empty evaluator/i)).toBeTruthy();
    expect(screen.queryByText(/several seconds/i)).toBeNull();
    expect(screen.queryByText(/nothing is saved/i)).toBeNull();

    await user.click(screen.getByRole("tab", { name: "From criteria" }));

    // Criteria mode explains the draft -> review -> save flow and warns it is slow.
    expect(screen.getByText(/version editor/i)).toBeTruthy();
    expect(screen.getByText(/nothing is saved/i)).toBeTruthy();
    expect(screen.getByText(/several seconds/i)).toBeTruthy();
    expect(screen.queryByText(/empty evaluator/i)).toBeNull();
  });

  it("has a tooltip beside the Criteria label", async () => {
    vi.mocked(evaluators.list).mockResolvedValue([]);
    vi.mocked(api).mockResolvedValue(config);
    const user = await openModal();

    await user.click(screen.getByRole("tab", { name: "From criteria" }));

    // The hand-rolled Tooltip exposes an info trigger; clicking it reveals a popover
    // explaining the criteria becomes the first version.
    const trigger = screen.getByRole("button", { name: "More information" });
    await user.click(trigger);
    expect(screen.getByRole("tooltip")).toBeTruthy();
  });

  it("keeps tab roles and aria-selected in sync as the mode changes", async () => {
    vi.mocked(evaluators.list).mockResolvedValue([]);
    vi.mocked(api).mockResolvedValue(config);
    const user = await openModal();

    expect(screen.getByRole("tab", { name: "From scratch", selected: true })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "From criteria", selected: false })).toBeTruthy();

    await user.click(screen.getByRole("tab", { name: "From criteria" }));

    expect(screen.getByRole("tab", { name: "From criteria", selected: true })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "From scratch", selected: false })).toBeTruthy();
  });

  it("labels the primary action 'Create' in scratch mode and 'Generate' in criteria mode", async () => {
    vi.mocked(evaluators.list).mockResolvedValue([]);
    vi.mocked(api).mockResolvedValue(config);
    const user = await openModal();

    expect(screen.getByRole("button", { name: "Create" })).toBeTruthy();

    await user.click(screen.getByRole("tab", { name: "From criteria" }));

    expect(screen.getByRole("button", { name: "Generate" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Create" })).toBeNull();
  });
});

// -- Gateway gating -----------------------------------------------------------
// Only the criteria-mode Generate action calls a model through the gateway; scratch-mode
// authoring (Create) must stay fully usable even when the gateway key is unset.

describe("EvaluatorsPage: gateway gating", () => {
  async function openModal() {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "New evaluator" }));
    return user;
  }

  it("still allows creating an evaluator in scratch mode when the gateway is not ready", async () => {
    useSetupMock.mockReturnValue(makeSetupResult({ gatewayReady: false }));
    vi.mocked(evaluators.list).mockResolvedValue([]);
    vi.mocked(api).mockResolvedValue(config);
    vi.mocked(evaluators.create).mockResolvedValue(makeEvaluator({ id: "e-scratch" }));
    const user = await openModal();

    // Scratch mode (the default) is untouched by the gateway gate.
    expect(screen.queryByText(GATEWAY_BLOCKER)).toBeNull();

    await user.type(screen.getByLabelText("Evaluator name"), "My evaluator");
    const create = screen.getByRole("button", { name: "Create" });
    expect(create).not.toBeDisabled();

    await user.click(create);

    await waitFor(() =>
      expect(evaluators.create).toHaveBeenCalledWith(
        expect.objectContaining({ name: "My evaluator" }),
      ),
    );
    expect(navigate).toHaveBeenCalledWith("/evaluators/e-scratch");
  });

  it("disables Generate and shows the shared gateway blocker in criteria mode when not ready", async () => {
    useSetupMock.mockReturnValue(makeSetupResult({ gatewayReady: false }));
    vi.mocked(evaluators.list).mockResolvedValue([]);
    vi.mocked(api).mockResolvedValue(config);
    const user = await openModal();

    await user.click(screen.getByRole("tab", { name: "From criteria" }));
    await user.type(screen.getByLabelText("Evaluator name"), "Criteria eval");
    await user.type(screen.getByLabelText("Criteria"), "A good answer is concise.");

    expect(screen.getByText(GATEWAY_BLOCKER)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
    expect(evaluators.generate).not.toHaveBeenCalled();
  });

  it("shows no gateway blocker in scratch mode even when the gateway is not ready", async () => {
    useSetupMock.mockReturnValue(makeSetupResult({ gatewayReady: false }));
    vi.mocked(evaluators.list).mockResolvedValue([]);
    vi.mocked(api).mockResolvedValue(config);
    await openModal();

    // Scratch mode is the default tab; no gateway blocker should leak in from criteria mode.
    expect(screen.queryByText(GATEWAY_BLOCKER)).toBeNull();
  });

  it("switching from a blocked criteria mode back to scratch clears the gateway blocker", async () => {
    useSetupMock.mockReturnValue(makeSetupResult({ gatewayReady: false }));
    vi.mocked(evaluators.list).mockResolvedValue([]);
    vi.mocked(api).mockResolvedValue(config);
    const user = await openModal();

    await user.click(screen.getByRole("tab", { name: "From criteria" }));
    expect(screen.getByText(GATEWAY_BLOCKER)).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "From scratch" }));
    expect(screen.queryByText(GATEWAY_BLOCKER)).toBeNull();
  });

  it("governs Generate only by its own validity (name + criteria) once the gateway is ready", async () => {
    useSetupMock.mockReturnValue(makeSetupResult({ gatewayReady: true }));
    vi.mocked(evaluators.list).mockResolvedValue([]);
    vi.mocked(api).mockResolvedValue(config);
    const user = await openModal();

    await user.click(screen.getByRole("tab", { name: "From criteria" }));

    expect(screen.queryByText(GATEWAY_BLOCKER)).toBeNull();
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();

    await user.type(screen.getByLabelText("Evaluator name"), "Criteria eval");
    await user.type(screen.getByLabelText("Criteria"), "A good answer is concise.");

    expect(screen.getByRole("button", { name: "Generate" })).not.toBeDisabled();
  });
});
