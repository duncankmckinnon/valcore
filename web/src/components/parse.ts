// Client-side CSV/JSONL parsing used only to preview an upload before it is sent.
// The server re-parses authoritatively; this is a best-effort read for the UI.

export type ParsedFile = {
  kind: "csv" | "jsonl" | "package";
  columns: string[];
  rows: Record<string, string>[];
};

function parseCsvLine(line: string): string[] {
  const fields: string[] = [];
  let current = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i += 1) {
    const char = line[i];
    if (inQuotes) {
      if (char === '"') {
        if (line[i + 1] === '"') {
          current += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        current += char;
      }
    } else if (char === '"') {
      inQuotes = true;
    } else if (char === ",") {
      fields.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  fields.push(current);
  return fields;
}

function parseCsv(text: string): ParsedFile {
  const lines = text.split(/\r?\n/).filter((line) => line.trim() !== "");
  if (lines.length === 0) {
    return { kind: "csv", columns: [], rows: [] };
  }
  const columns = parseCsvLine(lines[0]).map((name) => name.trim());
  const rows = lines.slice(1).map((line) => {
    const values = parseCsvLine(line);
    const record: Record<string, string> = {};
    columns.forEach((column, index) => {
      record[column] = values[index] ?? "";
    });
    return record;
  });
  return { kind: "csv", columns, rows };
}

function parseJsonl(text: string): ParsedFile {
  const rows: Record<string, string>[] = [];
  const columns: string[] = [];
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const record = JSON.parse(trimmed) as Record<string, unknown>;
    const flat: Record<string, string> = {};
    for (const [key, value] of Object.entries(record)) {
      if (!columns.includes(key)) columns.push(key);
      flat[key] = typeof value === "string" ? value : JSON.stringify(value);
    }
    rows.push(flat);
  }
  return { kind: "jsonl", columns, rows };
}

type Case = { inputs?: Record<string, unknown> };

// A pydantic_evals dataset is any object with a `cases` array; a bundled valcore package is
// marked by `kind` and nests that dataset under `dataset`. Both preview from cases[].inputs.
function parsePackage(text: string): ParsedFile | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    // Not valid JSON as a whole (e.g. a JSONL file) — let the JSONL parser handle it.
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) return null;
  const doc = parsed as Record<string, unknown>;

  let cases: unknown;
  if (Array.isArray(doc.cases)) {
    cases = doc.cases;
  } else if (
    doc.kind === "valcore/eval-package" &&
    typeof doc.dataset === "object" &&
    doc.dataset !== null &&
    Array.isArray((doc.dataset as Record<string, unknown>).cases)
  ) {
    cases = (doc.dataset as Record<string, unknown>).cases;
  } else {
    return null;
  }

  const columns: string[] = [];
  const rows: Record<string, string>[] = [];
  for (const entry of cases as Case[]) {
    const inputs = entry.inputs ?? {};
    const flat: Record<string, string> = {};
    for (const [key, value] of Object.entries(inputs)) {
      if (!columns.includes(key)) columns.push(key);
      flat[key] = typeof value === "string" ? value : JSON.stringify(value);
    }
    rows.push(flat);
  }
  return { kind: "package", columns, rows };
}

/** Parse a CSV, JSONL, or eval-package string into inferred columns and row records for preview. */
export function parseFile(filename: string, text: string): ParsedFile {
  const lower = filename.toLowerCase();
  if (lower.endsWith(".json")) {
    // `.json` is ambiguous: a package, a pydantic_evals dataset, or plain JSONL. Try the
    // structured shapes first; anything unrecognized falls through to the JSONL parser.
    const pkg = parsePackage(text);
    if (pkg) return pkg;
    return parseJsonl(text);
  }
  if (lower.endsWith(".jsonl")) {
    return parseJsonl(text);
  }
  return parseCsv(text);
}
