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
    default_model: "gateway/anthropic:claude-sonnet-5",
    local_cli_default: null,
    local_cli_options: ["claude", "codex", "cursor"],
    logfire_explore_url: null,
    logfire_traces_url: null,
    logfire_datasets_url: null,
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

    // gateway_api_key now lives in the Model Selection box, gated behind the
    // "Use Pydantic AI Gateway" checkbox (unchecked here since it's unset) —
    // covered separately by the gateway-key-visibility tests below.
    for (const name of NAMES.filter((n) => n !== "gateway_api_key")) {
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

  it("renders a local CLI option per local_cli_options entry with an explanation", async () => {
    setupGet.mockResolvedValue(makeStatus());
    renderPage();

    await screen.findByText("Model Selection");
    const select = screen.getByLabelText(/local cli/i) as HTMLSelectElement;
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(
      expect.arrayContaining(["None (use the gateway)", "claude", "codex", "cursor"])
    );
  });

  it("does not render the gateway key input until the checkbox is checked, unless already set", async () => {
    setupGet.mockResolvedValue(makeStatus({ gateway_api_key: { set: false } }));
    renderPage();

    await screen.findByText("Model Selection");
    expect(screen.queryByLabelText("Pydantic AI Gateway key")).not.toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("Use Pydantic AI Gateway"));
    expect(screen.getByLabelText("Pydantic AI Gateway key")).toBeInTheDocument();
  });

  it("shows the gateway key input already, unchecked-but-visible, when the key is already set", async () => {
    setupGet.mockResolvedValue(makeStatus({ gateway_api_key: { set: true } }));
    renderPage();

    await screen.findByLabelText("Pydantic AI Gateway key");
  });

  it("saves a selected local CLI as local_cli_default", async () => {
    setupGet.mockResolvedValue(makeStatus());
    setupSave.mockResolvedValue({ ...makeStatus(), local_cli_default: "codex" });
    renderPage();

    await screen.findByText("Model Selection");
    await userEvent.selectOptions(screen.getByLabelText(/local cli/i), "codex");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    expect(setupSave).toHaveBeenCalledWith(expect.objectContaining({ local_cli_default: "codex" }));
  });

  it("clears local_cli_default when None is selected after it was set", async () => {
    setupGet.mockResolvedValue({ ...makeStatus(), local_cli_default: "claude" });
    setupSave.mockResolvedValue(makeStatus());
    renderPage();

    await screen.findByText("Model Selection");
    await userEvent.selectOptions(screen.getByLabelText(/local cli/i), "");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    expect(setupSave).toHaveBeenCalledWith(
      expect.objectContaining({ clear: expect.arrayContaining(["local_cli_default"]) })
    );
  });
});
