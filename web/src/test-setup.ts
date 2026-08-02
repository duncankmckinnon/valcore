import { expect } from "vitest";

// jest-dom is not a dependency; the component suite only needs a couple of DOM
// matchers, so provide minimal versions rather than pulling in the whole package.
expect.extend({
  toBeDisabled(received: unknown) {
    const disabled =
      received instanceof HTMLElement &&
      "disabled" in received &&
      (received as HTMLButtonElement).disabled === true;
    return {
      pass: disabled,
      message: () => `expected element ${disabled ? "not " : ""}to be disabled`,
    };
  },
  toHaveTextContent(received: unknown, expected: string) {
    const text = received instanceof HTMLElement ? (received.textContent ?? "") : "";
    const pass = text.includes(expected);
    return {
      pass,
      message: () =>
        `expected element text ${pass ? "not " : ""}to contain "${expected}" (got "${text}")`,
    };
  },
});

declare module "vitest" {
  // The type parameter must match vitest's own `Assertion` declaration exactly.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  interface Assertion<T = any> {
    toBeDisabled(): T;
    toHaveTextContent(expected: string): T;
  }
  interface AsymmetricMatchersContaining {
    toBeDisabled(): void;
    toHaveTextContent(expected: string): void;
  }
}

// jsdom (v25) ships Blob/File without the standard async read helpers that the
// upload flow relies on (Blob.text). Polyfill them so component tests can exercise
// client-side file parsing the same way a real browser would.

if (typeof Blob !== "undefined" && typeof Blob.prototype.text !== "function") {
  Blob.prototype.text = function text(this: Blob): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsText(this);
    });
  };
}

if (typeof Blob !== "undefined" && typeof Blob.prototype.arrayBuffer !== "function") {
  Blob.prototype.arrayBuffer = function arrayBuffer(this: Blob): Promise<ArrayBuffer> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as ArrayBuffer);
      reader.onerror = () => reject(reader.error);
      reader.readAsArrayBuffer(this);
    });
  };
}
