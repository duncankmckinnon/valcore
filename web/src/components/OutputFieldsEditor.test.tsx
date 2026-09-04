import { useState } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OutputFieldsEditor } from "./OutputFieldsEditor";
import type { OutputField } from "../api/types";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function enumField(overrides: Partial<OutputField> = {}): OutputField {
  return {
    name: "verdict",
    type: "enum",
    description: "",
    required: true,
    enum_values: [],
    minimum: null,
    maximum: null,
    ...overrides,
  };
}

// A controlled harness so typing accumulates: each onChange feeds the next render's
// fields, exactly as VersionEditor wires it up.
function renderStateful(initial: OutputField[], onChange: (fields: OutputField[]) => void) {
  function Harness() {
    const [fields, setFields] = useState<OutputField[]>(initial);
    return (
      <OutputFieldsEditor
        fields={fields}
        onChange={(next) => {
          onChange(next);
          setFields(next);
        }}
      />
    );
  }
  render(<Harness />);
}

describe("OutputFieldsEditor", () => {
  it("keeps commas so multiple enum values can be typed", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    renderStateful([enumField()], onChange);

    await user.type(screen.getByLabelText("Field 0 enum values"), "pass, fail");

    expect(onChange).toHaveBeenLastCalledWith([
      enumField({ enum_values: ["pass", "fail"] }),
    ]);
    expect(screen.getByLabelText("Field 0 enum values")).toHaveValue("pass, fail");
  });
});
