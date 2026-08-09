import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RefinePanel } from "./RefinePanel";
import { evaluators } from "../api/client";
import { GATEWAY_BLOCKER, useSetup } from "./useSetup";
import type { UseSetupResult } from "./useSetup";
import type { GeneratedConfig } from "../api/types";

vi.mock("../api/client", () => ({
  evaluators: {
    refine: vi.fn(),
  },
}));

// Refining calls the model through the gateway, so it is gated the same way generation
// and runs are; the hook is mocked directly rather than driven through a fetch, matching
// the pattern used by the other gated forms' test suites.
vi.mock("./useSetup", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./useSetup")>();
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

const config: GeneratedConfig = {
  name: "Judge",
  version_name: "v1",
  instructions: "Judge the answer.",
  prompt_template: "{answer}",
  required_columns: ["answer"],
  output_fields: [
    {
      name: "verdict",
      type: "enum",
      description: "the verdict",
      required: true,
      enum_values: ["pass", "fail"],
      minimum: null,
      maximum: null,
    },
  ],
  score_field: "verdict",
  score_kind: "categorical",
  score_labels: ["pass", "fail"],
  score_minimum: null,
  score_maximum: null,
  capabilities: [],
  tools: [],
  rationale: "",
};

afterEach(() => {
  vi.clearAllMocks();
});

beforeEach(() => {
  useSetupMock.mockReturnValue(makeSetupResult());
});

describe("RefinePanel", () => {
  it("does nothing when the instruction is empty", async () => {
    const user = userEvent.setup();
    render(<RefinePanel config={config} onApply={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Refine" }));

    expect(evaluators.refine).not.toHaveBeenCalled();
  });

  it("refines the current config and renders a diff of the changed fields", async () => {
    vi.mocked(evaluators.refine).mockResolvedValue({
      config: { ...config, instructions: "Be stricter." },
      changed_fields: ["instructions"],
      summary: "",
    });
    const user = userEvent.setup();
    render(<RefinePanel config={config} onApply={vi.fn()} />);

    await user.type(screen.getByLabelText("Refine instruction"), "make it stricter");
    await user.click(screen.getByRole("button", { name: "Refine" }));

    expect(evaluators.refine).toHaveBeenCalledWith(
      expect.objectContaining({ config, instruction: "make it stricter" }),
    );
    expect(await screen.findByText("instructions")).not.toBeNull();
  });

  it("fires onApply with the accepted field's proposed value", async () => {
    vi.mocked(evaluators.refine).mockResolvedValue({
      config: { ...config, instructions: "Be stricter." },
      changed_fields: ["instructions"],
      summary: "",
    });
    const onApply = vi.fn();
    const user = userEvent.setup();
    render(<RefinePanel config={config} onApply={onApply} />);

    await user.type(screen.getByLabelText("Refine instruction"), "make it stricter");
    await user.click(screen.getByRole("button", { name: "Refine" }));
    await user.click(await screen.findByLabelText("Accept instructions"));

    expect(onApply).toHaveBeenCalledWith({ instructions: "Be stricter." });
  });
});

// -- Gateway gating -----------------------------------------------------------
// Refining calls a model through the gateway, so it needs the key exactly as generation
// and run-launching do.

describe("RefinePanel gateway gating", () => {
  it("disables Refine and shows the shared gateway blocker when the gateway key is unset", async () => {
    useSetupMock.mockReturnValue(makeSetupResult({ gatewayReady: false }));
    const user = userEvent.setup();
    render(<RefinePanel config={config} onApply={vi.fn()} />);

    await user.type(screen.getByLabelText("Refine instruction"), "make it stricter");

    expect(screen.getByText(GATEWAY_BLOCKER)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refine" })).toBeDisabled();
    expect(evaluators.refine).not.toHaveBeenCalled();
  });

  it("shows no gateway blocker and keeps existing behavior when the gateway is ready", async () => {
    useSetupMock.mockReturnValue(makeSetupResult({ gatewayReady: true }));
    vi.mocked(evaluators.refine).mockResolvedValue({
      config: { ...config, instructions: "Be stricter." },
      changed_fields: ["instructions"],
      summary: "",
    });
    const user = userEvent.setup();
    render(<RefinePanel config={config} onApply={vi.fn()} />);

    expect(screen.queryByText(GATEWAY_BLOCKER)).toBeNull();
    expect(screen.getByRole("button", { name: "Refine" })).not.toBeDisabled();

    await user.type(screen.getByLabelText("Refine instruction"), "make it stricter");
    await user.click(screen.getByRole("button", { name: "Refine" }));

    expect(evaluators.refine).toHaveBeenCalledWith(
      expect.objectContaining({ config, instruction: "make it stricter" }),
    );
  });
});
