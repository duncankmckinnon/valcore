// Node builtins are typed by `./node-builtins.d.ts` — `@types/node` is an uninstalled
// optional peer dep and tsconfig pins `types` to `["vitest/globals"]`, so a local
// ambient shim keeps `tsc --noEmit` green without adding a dependency.
import { readdirSync, readFileSync, existsSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// The stylesheet is a single global file that every component consumes by class name.
// Fourteen sibling tasks reference classes they are forbidden to define here, so a name
// this file forgets ships as silently *unstyled* markup — the exact defect that already
// exists over a dozen times in the pre-redesign codebase, uncaught by any test. This
// suite is the guard: it reads the CSS and every component from disk and proves the set
// of classes rendered is a subset of the set defined.

const srcDir = dirname(fileURLToPath(import.meta.url));
const webDir = dirname(srcDir);
const cssPath = join(srcDir, "styles.css");

// Class families that are assembled at runtime from string interpolation, so their full
// names never appear as literals in any component and the used-set scan cannot see them.
// We list them explicitly as "known used" so the subset check still forces the stylesheet
// to define them — otherwise a missing `.btn-primary` would go unnoticed because no
// literal "btn-primary" exists anywhere to flag it.
//   - btn-{variant}   → Button renders `btn btn-${variant}`   (primary/secondary/danger)
//   - badge-{tone}    → Badge renders `badge badge-${tone}`   (neutral/success/warning/danger)
//   - modal-{size}    → Modal renders `modal modal-${size}`   (sm/md/lg)
const INTERPOLATED_CLASSES = [
  "btn-primary",
  "btn-secondary",
  "btn-danger",
  "badge-neutral",
  "badge-success",
  "badge-warning",
  "badge-danger",
  "modal-sm",
  "modal-md",
  "modal-lg",
];

// Every class the stylesheet class contract promises to the other tasks. These are
// consumed as literals by components that may not exist in this worktree yet, so the
// contract — not the current TSX — is the source of truth for what must be defined.
const CONTRACT_CLASSES = [
  // Previously undefined (the pre-redesign orphans this redesign must finally define).
  "input",
  "link-button",
  "modal-actions",
  "generate-form",
  "export-actions",
  "export-source",
  "version-editor",
  "version-editor-frozen",
  "version-editor-actions",
  "chips",
  "chip",
  "chip-add",
  "instructions",
  "tools",
  "tool-toggle",
  // Shell.
  "nav-brand-row",
  "nav-logo",
  "nav-section-label",
  "nav-icon",
  // Modal.
  "modal-description",
  "modal-footer",
  "modal-sm",
  "modal-md",
  "modal-lg",
  "modal-two-pane",
  "modal-side",
  // Tooltip.
  "tooltip-wrap",
  "tooltip-trigger",
  "tooltip-popover",
  // FormFooter.
  "form-footer",
  "form-footer-blocker",
  "form-footer-ready",
  "form-footer-actions",
  // PageHeader / EmptyState.
  "page-header-text",
  "page-title",
  "page-description",
  "empty-state",
  "empty-state-icon",
  "empty-state-text",
  // Overview.
  "overview-stats",
  "stat-card",
  "stat-card-value",
  "stat-card-label",
  "stat-card-sub",
  "overview-next",
  "next-card",
  "overview-empty",
  // Version editor.
  "editor-layout",
  "editor-main",
  "editor-rail",
  "editor-section",
  "editor-section-head",
  "editor-section-body",
  "editor-disclosure",
  // Preview pane.
  "preview-pane",
  "preview-code",
  "mix-bar-row",
  "mix-bar-label",
  "mix-bar-track",
  "mix-bar-fill",
  // Summary strip.
  "summary-strip",
  // Export modal.
  "export-format",
  "export-layout",
  "export-file",
  "export-file-name",
  "export-file-actions",
];

// Every design token the contract publishes on :root. Later tasks reference these in the
// CSS they cannot write but the markup they can; a token that goes missing breaks their
// styling as surely as a missing class.
const CONTRACT_TOKENS = [
  "--bg",
  "--panel",
  "--panel-sunk",
  "--border",
  "--border-strong",
  "--text",
  "--text-dim",
  "--muted",
  "--accent",
  "--accent-text",
  "--accent-wash",
  "--danger",
  "--success",
  "--warning",
  "--text-xs",
  "--text-sm",
  "--text-md",
  "--text-lg",
  "--text-h1",
  "--space-1",
  "--space-2",
  "--space-3",
  "--space-4",
  "--space-5",
  "--space-6",
  "--radius",
  "--radius-lg",
];

/** Recursively collect every `.tsx` file under `dir`. */
function tsxFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...tsxFiles(full));
    else if (entry.isFile() && entry.name.endsWith(".tsx")) out.push(full);
  }
  return out;
}

// A sentinel that cannot occur in a class name, used to blank out `${...}` interpolations
// inside template literals. Blanking to a non-whitespace mark (rather than a space) keeps
// a token glued to an interpolation — the `btn-` in `btn-${variant}` — from splitting off
// as a bogus partial class; any token still carrying the mark is dropped.
const INTERP = "￿";

/**
 * Extract class-name tokens from the expression inside a `className={...}` attribute,
 * handling the codebase's two dynamic idioms without leaking non-class strings.
 */
function tokensFromExpr(expr: string): string[] {
  const tokens: string[] = [];

  // Template literals: keep only the static text. Blank interpolations first so that
  // comparison strings living inside a `${...}` (e.g. `mode === "scratch"`) never leak in.
  for (const template of expr.match(/`[^`]*`/g) ?? []) {
    const body = template.slice(1, -1).replace(/\$\{[^}]*\}/g, INTERP);
    for (const tok of body.split(/\s+/)) {
      if (tok && !tok.includes(INTERP)) tokens.push(tok);
    }
  }

  // Plain string literals outside any template — the branches of ternaries such as
  // `isActive ? "nav-link active" : "nav-link"`. Templates are stripped first so their
  // interpolated comparison strings are not mistaken for ternary branches.
  const withoutTemplates = expr.replace(/`[^`]*`/g, " ");
  for (const literal of withoutTemplates.match(/"[^"]*"|'[^']*'/g) ?? []) {
    for (const tok of literal.slice(1, -1).split(/\s+/)) {
      if (tok) tokens.push(tok);
    }
  }

  return tokens;
}

/**
 * Read a brace-balanced `{...}` expression starting at `openIdx` (the opening brace),
 * tracking string/template context so a brace inside a string never closes it early.
 */
function readBalancedBraces(text: string, openIdx: number): string | null {
  let depth = 0;
  let str: string | null = null; // active string delimiter, or null in code context
  for (let i = openIdx; i < text.length; i++) {
    const c = text[i];
    if (str) {
      if (c === "\\") i++; // skip escaped char
      else if (c === str) str = null;
      continue;
    }
    if (c === '"' || c === "'" || c === "`") str = c;
    else if (c === "{") depth++;
    else if (c === "}" && --depth === 0) return text.slice(openIdx + 1, i);
  }
  return null;
}

/** Every class token rendered by a component file, static and dynamic idioms alike. */
function usedClassesInFile(text: string): string[] {
  const found: string[] = [];
  // JSX attribute only: the trailing `=` excludes `.className` property reads in tests.
  const re = /className\s*=\s*/g;
  while (re.exec(text) !== null) {
    const i = re.lastIndex;
    const ch = text[i];
    if (ch === '"' || ch === "'") {
      const end = text.indexOf(ch, i + 1);
      if (end === -1) continue;
      for (const tok of text.slice(i + 1, end).split(/\s+/)) {
        if (tok) found.push(tok);
      }
    } else if (ch === "{") {
      const expr = readBalancedBraces(text, i);
      if (expr !== null) found.push(...tokensFromExpr(expr));
    }
  }
  return found;
}

/**
 * Every class defined by the stylesheet. A class is "defined" when it appears in selector
 * position: before a `{`, outside any declaration block. Splitting on `{` and reading only
 * the tail after the previous block's `}` guarantees declaration bodies — where
 * `color-mix(...)` values and property names live — are never scanned.
 */
function definedClasses(css: string): Set<string> {
  const noComments = css.replace(/\/\*[\s\S]*?\*\//g, " ");
  const classes = new Set<string>();
  const pieces = noComments.split("{");
  for (let k = 0; k < pieces.length - 1; k++) {
    const piece = pieces[k];
    const close = piece.lastIndexOf("}");
    const selector = close === -1 ? piece : piece.slice(close + 1);
    // A leading `[A-Za-z_]` rejects fragments of decimals like `.4fr` in `1.4fr` values.
    for (const match of selector.matchAll(/\.([A-Za-z_][\w-]*)/g)) classes.add(match[1]);
  }
  return classes;
}

/** Every custom property declared (`--name:`) in the stylesheet. */
function definedTokens(css: string): Set<string> {
  const noComments = css.replace(/\/\*[\s\S]*?\*\//g, " ");
  const tokens = new Set<string>();
  for (const match of noComments.matchAll(/(--[A-Za-z0-9-]+)\s*:/g)) tokens.add(match[1]);
  return tokens;
}

describe("stylesheet class contract", () => {
  const css = readFileSync(cssPath, "utf8");
  const defined = definedClasses(css);

  it("defines every class referenced in TSX (no orphans)", () => {
    // Map each used class to the files that reference it, so a failure names the offenders.
    const usage = new Map<string, Set<string>>();
    const record = (cls: string, source: string) => {
      const files = usage.get(cls) ?? new Set<string>();
      files.add(source);
      usage.set(cls, files);
    };

    for (const file of tsxFiles(srcDir)) {
      const rel = file.slice(webDir.length + 1);
      for (const cls of usedClassesInFile(readFileSync(file, "utf8"))) record(cls, rel);
    }
    for (const cls of INTERPOLATED_CLASSES) record(cls, "(runtime interpolation)");

    const orphans = [...usage.entries()]
      .filter(([cls]) => !defined.has(cls))
      .map(([cls, files]) => `  .${cls}  ←  ${[...files].sort().join(", ")}`)
      .sort();

    expect(
      orphans,
      `${orphans.length} class(es) used in markup but never defined in styles.css:\n${orphans.join("\n")}`,
    ).toEqual([]);
  });

  it("defines every class promised by the contract table", () => {
    const missing = CONTRACT_CLASSES.filter((cls) => !defined.has(cls)).sort();
    expect(
      missing,
      `${missing.length} contract class(es) missing from styles.css: ${missing.map((c) => "." + c).join(" ")}`,
    ).toEqual([]);
  });

  it("declares every design token promised by the contract", () => {
    const tokens = definedTokens(css);
    const missing = CONTRACT_TOKENS.filter((tok) => !tokens.has(tok)).sort();
    expect(
      missing,
      `${missing.length} contract token(s) missing from styles.css: ${missing.join(" ")}`,
    ).toEqual([]);
  });
});

describe("favicon", () => {
  it("copies the logo into public/ as a non-empty binary", () => {
    const logo = join(webDir, "public", "logo.png");
    expect(existsSync(logo), "web/public/logo.png must exist").toBe(true);
    expect(statSync(logo).size).toBeGreaterThan(0);
  });

  it("links the favicon from index.html", () => {
    const html = readFileSync(join(webDir, "index.html"), "utf8");
    const linksLogo =
      /<link[^>]*rel=["']icon["'][^>]*href=["']\/logo\.png["']/i.test(html) ||
      /<link[^>]*href=["']\/logo\.png["'][^>]*rel=["']icon["']/i.test(html);
    expect(linksLogo, 'index.html must declare <link rel="icon" href="/logo.png">').toBe(true);
  });
});
