import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import RunProgress from "./RunProgress";
import type { RunStreamEvent } from "../api/types";
import { runs } from "../api/client";

vi.mock("../api/client", () => ({
  runs: {
    streamEvents: vi.fn(),
    cancel: vi.fn(),
  },
}));

const streamMock = vi.mocked(runs.streamEvents);
const cancelMock = vi.mocked(runs.cancel);

// Capture the onEvent callback handed to streamEvents so tests can drive events.
function lastOnEvent(): (event: RunStreamEvent) => void {
  const call = streamMock.mock.calls[streamMock.mock.calls.length - 1];
  return call[1] as (event: RunStreamEvent) => void;
}

function emit(event: RunStreamEvent) {
  act(() => lastOnEvent()(event));
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RunProgress", () => {
  it("initializes from the replayed status and count rather than zero", () => {
    streamMock.mockReturnValue(() => {});

    render(<RunProgress runId="r1" total={10} />);
    expect(screen.getByText("0 / 10")).toBeTruthy();

    emit({ type: "status", status: "running", completed: 3 });

    expect(screen.getByText("3 / 10")).toBeTruthy();
  });

  it("increments the completed count on row events", () => {
    streamMock.mockReturnValue(() => {});

    render(<RunProgress runId="r1" total={10} />);
    emit({ type: "status", status: "running", completed: 3 });

    emit({ type: "row", row_id: "x", success: true, score_value: "good" });

    expect(screen.getByText("4 / 10")).toBeTruthy();
  });

  it("renders the terminal state on a finished event", () => {
    streamMock.mockReturnValue(() => {});

    render(<RunProgress runId="r1" total={10} />);
    emit({ type: "started", total: 10 });
    emit({ type: "finished", status: "completed", metrics: null });

    expect(screen.getByText("completed")).toBeTruthy();
    // The Cancel button disappears once the run is terminal.
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  });

  it("calls the unsubscribe function on unmount", () => {
    const unsubscribe = vi.fn();
    streamMock.mockReturnValue(unsubscribe);

    const { unmount } = render(<RunProgress runId="r1" total={10} />);
    expect(unsubscribe).not.toHaveBeenCalled();

    unmount();

    expect(unsubscribe).toHaveBeenCalledOnce();
  });

  it("calls runs.cancel when Cancel is clicked", async () => {
    streamMock.mockReturnValue(() => {});
    cancelMock.mockResolvedValue({} as never);

    render(<RunProgress runId="r1" total={10} />);
    emit({ type: "status", status: "running", completed: 0 });

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(cancelMock).toHaveBeenCalledWith("r1"));
  });
});
