// Tests for useSetup, the one hook that every gateway-gated action (generation, runs) reads to
// decide whether to render disabled with the shared GATEWAY_BLOCKER hint. The central contract
// is the don't-strand-the-user guarantee: gatewayReady defaults to true and stays true through a
// transient loading state or a rejected fetch, since the hook is a helpful hint, not the actual
// enforcement (the server-side guard is what really blocks the request). These tests render the
// hook inside a probe component and assert via role/text queries, matching this project's
// convention of never querying by CSS class. The stale-response guard test mirrors the
// equivalent case in ExportModal.test.tsx, which exercises the same cancelled-flag pattern.

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GATEWAY_BLOCKER, useSetup } from "./useSetup";
import { setup } from "../api/client";
import type { SetupKey, SetupStatus } from "../api/types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    setup: { ...actual.setup, get: vi.fn() },
  };
});

const setupGet = vi.mocked(setup.get);

// A promise whose resolution is driven by the test, used to interleave in-flight fetches and
// to observe the hook's state while a request is still pending.
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

// Builds a SetupStatus with all three known keys, overriding only the `set` flag for each so a
// test can flip just the one bit it cares about.
function makeStatus(overrides: Partial<Record<SetupKey["name"], boolean>> = {}): SetupStatus {
  const defaults: Record<SetupKey["name"], boolean> = {
    gateway_api_key: true,
    logfire_token: false,
    logfire_api_key: false,
  };
  const set = { ...defaults, ...overrides };
  const names: SetupKey["name"][] = ["gateway_api_key", "logfire_token", "logfire_api_key"];
  return {
    keys: names.map((name) => ({
      name,
      set: set[name],
      required: name === "gateway_api_key",
      label: name,
      command: `valcore config set ${name} ...`,
      purpose: `used for ${name}`,
    })),
  };
}

// Renders the hook's return value as plain text/role nodes a test can query the way this
// project's component tests already do, rather than reaching for renderHook internals.
function Probe() {
  const { status, gatewayReady, loading, error, refetch } = useSetup();
  return (
    <div>
      <p role="status">{loading ? "loading" : "idle"}</p>
      <p>{gatewayReady ? "gateway ready" : "gateway blocked"}</p>
      <p>{error !== null ? `error:${String(error)}` : "no error"}</p>
      <p>{status ? `keys:${status.keys.length}` : "no status"}</p>
      <button type="button" onClick={refetch}>
        Refetch
      </button>
    </div>
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("useSetup", () => {
  it("reports gatewayReady true once loaded when the gateway key is set", async () => {
    setupGet.mockResolvedValue(makeStatus({ gateway_api_key: true }));
    render(<Probe />);

    expect(await screen.findByText("gateway ready")).toBeInTheDocument();
    expect(screen.queryByText("gateway blocked")).toBeNull();
    expect(screen.getByText("keys:3")).toBeInTheDocument();
  });

  it("reports gatewayReady false once loaded when the gateway key is unset", async () => {
    setupGet.mockResolvedValue(makeStatus({ gateway_api_key: false }));
    render(<Probe />);

    expect(await screen.findByText("gateway blocked")).toBeInTheDocument();
    expect(screen.queryByText("gateway ready")).toBeNull();
  });

  it("keeps gatewayReady true while the initial fetch is still in flight", async () => {
    const pending = deferred<SetupStatus>();
    setupGet.mockReturnValue(pending.promise);
    render(<Probe />);

    expect(screen.getByRole("status")).toHaveTextContent("loading");
    // Not yet loaded, and the gateway key could turn out to be unset — the hook must not strand
    // the user with disabled buttons during this window.
    expect(screen.getByText("gateway ready")).toBeInTheDocument();

    pending.resolve(makeStatus({ gateway_api_key: false }));
    // Only once the fetch resolves does the real (false) value take over.
    expect(await screen.findByText("gateway blocked")).toBeInTheDocument();
  });

  it("keeps gatewayReady true after the initial fetch rejects", async () => {
    setupGet.mockRejectedValue(new Error("network down"));
    render(<Probe />);

    await screen.findByText(/error:/);
    expect(screen.getByRole("status")).toHaveTextContent("idle");
    expect(screen.getByText("gateway ready")).toBeInTheDocument();
    expect(screen.queryByText("gateway blocked")).toBeNull();
  });

  it("keeps gatewayReady true while a refetch is in flight, even after a loaded unset key", async () => {
    const user = userEvent.setup();
    const pending = deferred<SetupStatus>();
    setupGet.mockResolvedValueOnce(makeStatus({ gateway_api_key: false })).mockReturnValueOnce(pending.promise);
    render(<Probe />);

    // The initial load establishes a real (false) status — this is what exposed the bug: a
    // naive `gatewayKey?.set !== false` read of stale status ignores the in-flight refetch.
    await screen.findByText("gateway blocked");

    await user.click(screen.getByRole("button", { name: "Refetch" }));
    expect(screen.getByRole("status")).toHaveTextContent("loading");
    expect(screen.getByText("gateway ready")).toBeInTheDocument();
    expect(screen.queryByText("gateway blocked")).toBeNull();

    await act(async () => {
      pending.resolve(makeStatus({ gateway_api_key: false }));
      await Promise.resolve();
    });
    expect(await screen.findByText("gateway blocked")).toBeInTheDocument();
  });

  it("keeps gatewayReady true after a refetch rejects, even after a loaded unset key", async () => {
    const user = userEvent.setup();
    setupGet
      .mockResolvedValueOnce(makeStatus({ gateway_api_key: false }))
      .mockRejectedValueOnce(new Error("network down"));
    render(<Probe />);

    await screen.findByText("gateway blocked");

    await user.click(screen.getByRole("button", { name: "Refetch" }));

    await screen.findByText(/error:/);
    expect(screen.getByRole("status")).toHaveTextContent("idle");
    expect(screen.getByText("gateway ready")).toBeInTheDocument();
    expect(screen.queryByText("gateway blocked")).toBeNull();
  });

  it("refetch issues a second request and reflects the new value", async () => {
    const user = userEvent.setup();
    setupGet
      .mockResolvedValueOnce(makeStatus({ gateway_api_key: false }))
      .mockResolvedValueOnce(makeStatus({ gateway_api_key: true }));
    render(<Probe />);

    await screen.findByText("gateway blocked");
    await user.click(screen.getByRole("button", { name: "Refetch" }));

    expect(await screen.findByText("gateway ready")).toBeInTheDocument();
    expect(setupGet).toHaveBeenCalledTimes(2);
  });

  it("clears a stale error once a refetch succeeds", async () => {
    const user = userEvent.setup();
    setupGet
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce(makeStatus({ gateway_api_key: true }));
    render(<Probe />);

    await screen.findByText(/error:/);
    await user.click(screen.getByRole("button", { name: "Refetch" }));

    expect(await screen.findByText("no error")).toBeInTheDocument();
    expect(screen.getByText("gateway ready")).toBeInTheDocument();
  });

  it("keeps the later response in state when an earlier in-flight refetch resolves last", async () => {
    const user = userEvent.setup();
    const first = deferred<SetupStatus>();
    const second = deferred<SetupStatus>();
    setupGet
      .mockResolvedValueOnce(makeStatus({ gateway_api_key: true })) // initial mount fetch
      .mockReturnValueOnce(first.promise) // first refetch, stale
      .mockReturnValueOnce(second.promise); // second refetch, supersedes the first

    render(<Probe />);
    await screen.findByText("gateway ready");

    await user.click(screen.getByRole("button", { name: "Refetch" }));
    await user.click(screen.getByRole("button", { name: "Refetch" }));

    // The newer (second) request settles first, then the stale (first) one.
    second.resolve(makeStatus({ gateway_api_key: false }));
    expect(await screen.findByText("gateway blocked")).toBeInTheDocument();

    // Resolve and flush the stale promise inside act() so its (wrongly) resulting render, if
    // any, commits before the assertion below runs — awaiting bare microtasks outside act()
    // lets React defer that commit past the assertion and hide the bug.
    await act(async () => {
      first.resolve(makeStatus({ gateway_api_key: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText("gateway blocked")).toBeInTheDocument();
    expect(screen.queryByText("gateway ready")).toBeNull();
  });

  it("exposes GATEWAY_BLOCKER as a non-empty string mentioning the gateway key", () => {
    expect(typeof GATEWAY_BLOCKER).toBe("string");
    expect(GATEWAY_BLOCKER.length).toBeGreaterThan(0);
    expect(GATEWAY_BLOCKER.toLowerCase()).toContain("gateway");
  });
});
