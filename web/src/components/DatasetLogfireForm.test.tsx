import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DatasetLogfireForm from "./DatasetLogfireForm";
import { datasets } from "../api/client";
import type { DatasetCreated, SetupKey, SetupStatus } from "../api/types";
import { useSetup } from "./useSetup";
import type { UseSetupResult } from "./useSetup";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    datasets: { ...actual.datasets, fromLogfire: vi.fn() },
  };
});

vi.mock("./useSetup", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./useSetup")>();
  return { ...actual, useSetup: vi.fn() };
});

const fromLogfire = vi.mocked(datasets.fromLogfire);
const useSetupMock = vi.mocked(useSetup);

function makeStatus(overrides: {
  logfireKey?: boolean;
  exploreUrl?: string | null;
} = {}): SetupStatus {
  const names: SetupKey["name"][] = ["gateway_api_key", "logfire_token", "logfire_api_key"];
  return {
    keys: names.map((name) => ({
      name,
      set: name === "logfire_api_key" ? (overrides.logfireKey ?? true) : true,
      required: name === "gateway_api_key",
      label: name,
      command: `valcore config set ${name}`,
      purpose: name,
    })),
    logfire_explore_url: overrides.exploreUrl === undefined ? null : overrides.exploreUrl,
  };
}

function mockSetup(overrides: { logfireKey?: boolean; exploreUrl?: string | null } = {}): void {
  const result: UseSetupResult = {
    status: makeStatus(overrides),
    gatewayReady: true,
    loading: false,
    error: null,
    refetch: vi.fn(),
  };
  useSetupMock.mockReturnValue(result);
}

function madeCreated(): DatasetCreated {
  return {
    dataset: {
      id: "ds-1",
      created_at: "2026-09-03T00:00:00Z",
      name: "traces",
      description: "",
      columns: ["span_id"],
      label_schema: {},
      row_count: 1,
      labeled_count: 0,
    },
    row_count: 1,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DatasetLogfireForm", () => {
  it("disables Open SQL Workbench until the explore URL is set", () => {
    mockSetup({ exploreUrl: null });
    render(<DatasetLogfireForm onCreated={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Open SQL Workbench" })).toBeDisabled();
  });

  it("opens the workbench as a link when the URL is configured", () => {
    mockSetup({
      exploreUrl: "https://logfire-us.pydantic.dev/duncan/agent-tracing/explore",
    });
    render(<DatasetLogfireForm onCreated={vi.fn()} />);

    const link = screen.getByRole("link", { name: "Open SQL Workbench" });
    expect(link).toHaveAttribute(
      "href",
      "https://logfire-us.pydantic.dev/duncan/agent-tracing/explore",
    );
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("submits name, sql, and sample_n to fromLogfire", async () => {
    mockSetup();
    fromLogfire.mockResolvedValue(madeCreated());
    const onCreated = vi.fn();
    render(<DatasetLogfireForm onCreated={onCreated} />);

    await userEvent.type(screen.getByLabelText("Name"), "traces");
    await userEvent.type(screen.getByLabelText("SQL"), "SELECT span_id FROM records");
    await userEvent.click(screen.getByRole("button", { name: "Pull dataset" }));

    await waitFor(() => expect(fromLogfire).toHaveBeenCalledOnce());
    expect(fromLogfire).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "traces",
        sql: "SELECT span_id FROM records",
        sample_n: 20,
      }),
    );
    expect(onCreated).toHaveBeenCalledWith("ds-1");
  });

  it("blocks submit when the Logfire API key is unset", () => {
    mockSetup({ logfireKey: false });
    render(<DatasetLogfireForm onCreated={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Pull dataset" })).toBeDisabled();
    expect(screen.getByText(/valcore config set-logfire-key/)).toBeInTheDocument();
  });
});
