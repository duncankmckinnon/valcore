// Tests for the SQL-pull top-up modal. Coverage focuses on the prefill (stored pull settings
// become the starting point, except seed, which is deliberately left blank) and on submitting
// only what the form carries.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LogfirePullMoreRows from "./LogfirePullMoreRows";
import { datasets } from "../api/client";
import type { Dataset, DatasetLogfirePull, DatasetRow } from "../api/types";
import { useSetup } from "./useSetup";
import type { UseSetupResult } from "./useSetup";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    datasets: { ...actual.datasets, pullMoreFromLogfire: vi.fn() },
  };
});

vi.mock("./useSetup", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./useSetup")>();
  return { ...actual, useSetup: vi.fn() };
});

const pullMoreMock = vi.mocked(datasets.pullMoreFromLogfire);
const useSetupMock = vi.mocked(useSetup);

function mockReadKeySet(set: boolean): void {
  const result: UseSetupResult = {
    status: {
      keys: set
        ? [
            {
              name: "logfire_read_key",
              set: true,
              required: false,
              label: "Logfire read key",
              command: "valcore config set-logfire-read-key",
              purpose: "pull",
              explanation: "pull",
              from_env: false,
            },
          ]
        : [],
      default_model: "gateway/anthropic:claude-sonnet-5",
      local_cli_default: null,
      local_cli_options: [],
      logfire_explore_url: null,
      logfire_traces_url: null,
      logfire_datasets_url: null,
    },
    gatewayReady: true,
    loading: false,
    error: null,
    refetch: vi.fn(),
  };
  useSetupMock.mockReturnValue(result);
}

beforeEach(() => {
  mockReadKeySet(true);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function madeDataset(): Dataset {
  return {
    id: "d1",
    created_at: "2026-09-08T00:00:00Z",
    name: "From Logfire",
    description: "",
    columns: ["question"],
  };
}

function madePull(overrides: Partial<DatasetLogfirePull> = {}): DatasetLogfirePull {
  return {
    sql: "SELECT question FROM records",
    sample_n: 20,
    seed: 42,
    min_timestamp: "2026-09-01T00:00:00Z",
    max_timestamp: null,
    label_column: null,
    ...overrides,
  };
}

function madeRows(n: number): DatasetRow[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `r${i}`,
    dataset_id: "d1",
    idx: i,
    data: { question: "q" },
  }));
}

function renderModal(overrides: Partial<React.ComponentProps<typeof LogfirePullMoreRows>> = {}) {
  const props = {
    open: true,
    dataset: madeDataset(),
    pull: madePull(),
    onPulled: vi.fn(),
    onClose: vi.fn(),
    ...overrides,
  };
  render(<LogfirePullMoreRows {...props} />);
  return props;
}

describe("LogfirePullMoreRows", () => {
  it("prefills SQL, sample size, and the time window from the stored pull", () => {
    renderModal();

    expect(screen.getByLabelText("SQL")).toHaveValue("SELECT question FROM records");
    expect(screen.getByLabelText("Sample size")).toHaveValue(20);
    expect(screen.getByLabelText(/Min timestamp/)).toHaveValue("2026-09-01T00:00:00Z");
    expect(screen.getByLabelText(/Max timestamp/)).toHaveValue("");
  });

  it("leaves the seed blank even though the pull stored one", () => {
    renderModal();

    expect(screen.getByLabelText(/Seed/)).toHaveValue("");
  });

  it("sends the prefilled fields back on submit with no seed", async () => {
    const user = userEvent.setup();
    pullMoreMock.mockResolvedValue(madeRows(3));
    renderModal();

    await user.click(screen.getByRole("button", { name: "Pull" }));

    await waitFor(() => expect(pullMoreMock).toHaveBeenCalled());
    expect(pullMoreMock.mock.calls[0]).toEqual([
      "d1",
      {
        sql: "SELECT question FROM records",
        sample_n: 20,
        min_timestamp: "2026-09-01T00:00:00Z",
      },
    ]);
  });

  it("sends a typed seed when the user sets one", async () => {
    const user = userEvent.setup();
    pullMoreMock.mockResolvedValue(madeRows(1));
    renderModal();

    await user.type(screen.getByLabelText(/Seed/), "7");
    await user.click(screen.getByRole("button", { name: "Pull" }));

    await waitFor(() => expect(pullMoreMock).toHaveBeenCalled());
    expect(pullMoreMock.mock.calls[0]?.[1]?.seed).toBe(7);
  });

  it("reports how many rows were added", async () => {
    const user = userEvent.setup();
    pullMoreMock.mockResolvedValue(madeRows(4));
    const props = renderModal();

    await user.click(screen.getByRole("button", { name: "Pull" }));

    await waitFor(() => expect(props.onPulled).toHaveBeenCalledWith(4));
  });

  it("blocks Pull and disables it when the SQL is emptied", async () => {
    const user = userEvent.setup();
    renderModal();

    const sql = screen.getByLabelText("SQL");
    await user.clear(sql);

    expect(screen.getByRole("status").textContent).toMatch(/sql/i);
    expect(screen.getByRole("button", { name: "Pull" })).toBeDisabled();
  });

  it("disables Pull and explains why when the Logfire read key is not set", () => {
    mockReadKeySet(false);
    renderModal();

    const blocker = screen.getByRole("status");
    expect(blocker.textContent).toMatch(/read key/i);
    expect(screen.getByRole("button", { name: "Pull" })).toBeDisabled();
  });

  it("keeps the form filled in when the pull fails", async () => {
    const user = userEvent.setup();
    pullMoreMock.mockRejectedValue(new Error("boom"));
    const props = renderModal();

    await user.click(screen.getByRole("button", { name: "Pull" }));

    await waitFor(() =>
      expect(screen.getByLabelText("SQL")).toHaveValue("SELECT question FROM records"),
    );
    expect(props.onPulled).not.toHaveBeenCalled();
  });
});
