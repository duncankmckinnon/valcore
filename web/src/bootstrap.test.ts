import { expect, it, vi } from "vitest";
import { startApp } from "./bootstrap";

it("renders the application even when optional telemetry initialization fails", async () => {
  const render = vi.fn();
  const initialize = vi.fn(async () => {
    throw new Error("telemetry unavailable");
  });
  await startApp(render, initialize);

  expect(initialize).toHaveBeenCalledOnce();
  expect(render).toHaveBeenCalledOnce();
});

it("renders after the timeout when optional telemetry initialization stalls", async () => {
  vi.useFakeTimers();
  try {
    const render = vi.fn();
    const startup = startApp(render, () => new Promise(() => undefined), 2_000);

    await vi.advanceTimersByTimeAsync(2_000);
    await startup;

    expect(render).toHaveBeenCalledOnce();
  } finally {
    vi.useRealTimers();
  }
});
