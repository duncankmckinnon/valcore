import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import EvaluatorsPage from "./EvaluatorsPage";
import { api, evaluators } from "../api/client";
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
