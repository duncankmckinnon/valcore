// Client-side CSV/JSONL parsing used only to preview an upload before it is sent.
// The server re-parses authoritatively; this is a best-effort read for the UI.

export type ParsedFile = {
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
    return { columns: [], rows: [] };
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
  return { columns, rows };
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
  return { columns, rows };
}

/** Parse a CSV or JSONL string into inferred columns and row records for preview. */
export function parseFile(filename: string, text: string): ParsedFile {
  const lower = filename.toLowerCase();
  if (lower.endsWith(".jsonl") || lower.endsWith(".json")) {
    return parseJsonl(text);
  }
  return parseCsv(text);
}
