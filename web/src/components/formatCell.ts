// Dataset tables share one representation for scalar and structured cell values so
// read-only overlays do not drift from the editable dataset grid.

/** Convert an arbitrary dataset cell value to its table display representation. */
export function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value, null, 2);
}
