import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import SettingsPage from "./SettingsPage";
import { setup } from "../api/client";
import type { SetupKey, SetupKeyName, SetupStatus } from "../api/types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    setup: { ...actual.setup, get: vi.fn(), save: vi.fn() },
  };
});

const setupGet = vi.mocked(setup.get);
const setupSave = vi.mocked(setup.save);

const NAMES: SetupKeyName[] = [
  "gateway_api_key",
  "logfire_token",
  "logfire_read_key",
  "logfire_write_key",
];

const LABELS: Record<SetupKeyName, string> = {
  gateway_api_key: "Pydantic AI Gateway key",
  logfire_token: "Logfire tracing token",
  logfire_read_key: "Logfire read key",
  logfire_write_key: "Logfire write key",
};

function makeKey(name: SetupKeyName, overrides: Partial<SetupKey> = {}): SetupKey {
  return {
    name,
    set: false,
    required: name === "gateway_api_key",
    label: LABELS[name],
    command: `valcore config set ${name}`,
    purpose: `${name} purpose`,
    explanation: `${name} explanation covering use and scope.`,
    from_env: false,
    ...overrides,
  };
}

function makeStatus(overrides: Partial<Record<SetupKeyName, Partial<SetupKey>>> = {}): SetupStatus {
  return {
    keys: NAMES.map((name) => makeKey(name, overrides[name])),
    logfire_explore_url: null,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <SettingsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  setupGet.mockResolvedValue(makeStatus());
  setupSave.mockResolvedValue(makeStatus());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SettingsPage", () => {
  it("renders a password input per key, empty when unset, with each explanation", async () => {
    renderPage();

    for (const name of NAMES) {
      const input = await screen.findByLabelText(LABELS[name]);
      expect(input).toHaveAttribute("type", "password");
      expect(input).toHaveValue("");
      expect(screen.getByText(`${name} explanation covering use and scope.`)).toBeTruthy();
    }
  });

  it("shows a masked value in a set key and leaves unset keys empty", async () => {
    setupGet.mockResolvedValue(
      makeStatus({
        gateway_api_key: { set: true },
        logfire_read_key: { set: true },
      }),
    );
    renderPage();

    expect(await screen.findByLabelText(LABELS.gateway_api_key)).toHaveValue("••••••••");
    expect(screen.getByLabelText(LABELS.logfire_read_key)).toHaveValue("••••••••");
    expect(screen.getByLabelText(LABELS.logfire_token)).toHaveValue("");
    expect(screen.getByLabelText(LABELS.logfire_write_key)).toHaveValue("");
  });

  it("saves typed values and does not post the mask of an untouched set key", async () => {
    const user = userEvent.setup();
    setupGet.mockResolvedValue(makeStatus({ gateway_api_key: { set: true } }));
    setupSave.mockResolvedValue(makeStatus({ gateway_api_key: { set: true } }));
    renderPage();

    const read = await screen.findByLabelText(LABELS.logfire_read_key);
    await user.type(read, "lf-new-read");
    await user.click(screen.getByRole("button", { name: "Save keys" }));

    await waitFor(() => expect(setupSave).toHaveBeenCalledOnce());
    expect(setupSave).toHaveBeenCalledWith({ logfire_read_key: "lf-new-read" });
  });

  it("saves the same value to read and write when the shared-key option is checked", async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByLabelText(LABELS.logfire_read_key);
    await user.click(screen.getByRole("checkbox", { name: /use the same key for read and write/i }));
    await user.type(screen.getByLabelText(LABELS.logfire_read_key), "lf-combined");
    await user.click(screen.getByRole("button", { name: "Save keys" }));

    await waitFor(() => expect(setupSave).toHaveBeenCalledOnce());
    expect(setupSave).toHaveBeenCalledWith({
      logfire_read_key: "lf-combined",
      logfire_write_key: "lf-combined",
    });
  });

  it("clears a set key on save after Clear is clicked", async () => {
    const user = userEvent.setup();
    setupGet.mockResolvedValue(makeStatus({ logfire_token: { set: true } }));
    setupSave.mockResolvedValue(makeStatus());
    renderPage();

    const tokenRow = (await screen.findByLabelText(LABELS.logfire_token)).closest("li");
    expect(tokenRow).toBeTruthy();
    await user.click(within(tokenRow as HTMLElement).getByRole("button", { name: "Clear" }));
    expect(screen.getByLabelText(LABELS.logfire_token)).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "Save keys" }));

    await waitFor(() => expect(setupSave).toHaveBeenCalledOnce());
    expect(setupSave).toHaveBeenCalledWith({ clear: ["logfire_token"] });
  });

  it("notes when a key is set from the environment", async () => {
    setupGet.mockResolvedValue(makeStatus({ gateway_api_key: { set: true, from_env: true } }));
    renderPage();

    expect(await screen.findByText(/set from the environment/i)).toBeTruthy();
  });
});
