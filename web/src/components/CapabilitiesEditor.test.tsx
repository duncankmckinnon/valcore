import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CapabilitiesEditor } from "./CapabilitiesEditor";
import type { CapabilitySpec } from "../api/types";

const available = ["FileSystem", "Shell"];

afterEach(() => {
  vi.clearAllMocks();
});

// A controlled harness so typing accumulates: each onChange feeds the next render's value,
// exactly as the real parent (VersionEditor) wires it up.
function renderStateful(initial: CapabilitySpec[], onChange: (value: CapabilitySpec[]) => void) {
  function Harness() {
    const [value, setValue] = useState<CapabilitySpec[]>(initial);
    return (
      <CapabilitiesEditor
        available={available}
        value={value}
        readOnly={false}
        onChange={(next) => {
          onChange(next);
          setValue(next);
        }}
      />
    );
  }
  render(<Harness />);
}

describe("CapabilitiesEditor", () => {
  it("hides capability config until the capability is enabled", () => {
    render(
      <CapabilitiesEditor available={available} value={[]} readOnly={false} onChange={vi.fn()} />,
    );

    expect(screen.queryByLabelText("FileSystem root dir")).toBeNull();
    expect(screen.queryByLabelText("Shell allowed commands")).toBeNull();
  });

  it("emits a new capability when FileSystem is toggled on", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <CapabilitiesEditor available={available} value={[]} readOnly={false} onChange={onChange} />,
    );

    await user.click(screen.getByRole("checkbox", { name: "FileSystem" }));

    expect(onChange).toHaveBeenCalledWith([{ name: "FileSystem", config: {} }]);
  });

  it("reveals the root dir input when FileSystem is enabled and emits its config", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    renderStateful([{ name: "FileSystem", config: {} }], onChange);

    const rootDir = screen.getByLabelText("FileSystem root dir");
    await user.type(rootDir, "/tmp");

    expect(onChange).toHaveBeenLastCalledWith([
      { name: "FileSystem", config: { root_dir: "/tmp" } },
    ]);
  });

  it("emits Shell allowed_commands as a trimmed list", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    renderStateful([{ name: "Shell", config: {} }], onChange);

    await user.type(screen.getByLabelText("Shell allowed commands"), "ls, cat");

    expect(onChange).toHaveBeenLastCalledWith([
      { name: "Shell", config: { allowed_commands: ["ls", "cat"] } },
    ]);
  });

  it("removes the capability when toggled off", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <CapabilitiesEditor
        available={available}
        value={[{ name: "FileSystem", config: { root_dir: "/tmp" } }]}
        readOnly={false}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByRole("checkbox", { name: "FileSystem" }));

    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("disables the checkboxes when read-only", () => {
    render(
      <CapabilitiesEditor
        available={available}
        value={[]}
        readOnly={true}
        onChange={vi.fn()}
      />,
    );

    expect((screen.getByRole("checkbox", { name: "FileSystem" }) as HTMLInputElement).disabled).toBe(
      true,
    );
  });
});
