// Ambient declarations for the Node builtins used by disk-reading tests (styles.test.ts).
//
// `@types/node` is an optional peer dependency that this project deliberately does not
// install, and tsconfig pins `types` to `["vitest/globals"]`. Adding the package would
// violate the project's "no new dependencies" rule. This file is a *standalone ambient*
// declaration (no top-level import/export, so it is a script, not a module) that types
// only the handful of APIs the tests call — enough to keep `tsc --noEmit` green.

declare module "node:fs" {
  interface Dirent {
    name: string;
    isDirectory(): boolean;
    isFile(): boolean;
  }
  export function readdirSync(path: string, opts: { withFileTypes: true }): Dirent[];
  export function readFileSync(path: string, encoding: "utf8"): string;
  export function existsSync(path: string): boolean;
  export function statSync(path: string): { size: number };
}

declare module "node:path" {
  export function dirname(path: string): string;
  export function join(...parts: string[]): string;
}

declare module "node:url" {
  export function fileURLToPath(url: string): string;
}
