// Offline mirror of the server-side `validate_version` (src/valcore/models.py), so the
// draft editor can enable Save only when the config would actually persist. Pure functions,
// no React, no API calls; the server stays authoritative and still returns 422 on mismatch.

import type { CapabilitySpec, OutputField, ScoreKind } from "../api/types";

export type VersionDraft = {
  version_name: string;
  model: string;
  instructions: string;
  prompt_template: string;
  required_columns: string[];
  output_fields: OutputField[];
  score_field: string;
  score_kind: ScoreKind;
  score_labels: string[] | null;
  score_minimum: number | null;
  score_maximum: number | null;
  capabilities: CapabilitySpec[];
  tools: string[];
};

/** Field name -> human-readable problem. Empty object means the draft would save. */
export type VersionErrors = Partial<Record<keyof VersionDraft, string>>;

const IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]*$/;

const NUMERIC_FIELD_TYPES: ReadonlySet<string> = new Set(["int", "float"]);

// Mirrors Python's `keyword.iskeyword`: a valid-looking identifier that is a reserved word
// (e.g. `class`) cannot be a field name because the server rejects it.
const PYTHON_KEYWORDS: ReadonlySet<string> = new Set([
  "False", "None", "True", "and", "as", "assert", "async", "await", "break", "class",
  "continue", "def", "del", "elif", "else", "except", "finally", "for", "from", "global",
  "if", "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise", "return",
  "try", "while", "with", "yield",
]);

function isValidFieldName(name: string): boolean {
  return IDENTIFIER.test(name) && !PYTHON_KEYWORDS.has(name);
}

// Extract the named `{placeholder}` fields of a prompt template, mirroring
// `string.Formatter().parse`: doubled braces are literal, an empty `{}` is unnamed, and any
// conversion (`!r`) or format spec (`:>10`) is stripped from the field name.
function templateColumns(template: string): Set<string> {
  const names = new Set<string>();
  let i = 0;
  while (i < template.length) {
    const char = template[i];
    if (char === "{") {
      if (template[i + 1] === "{") {
        i += 2;
        continue;
      }
      const end = template.indexOf("}", i + 1);
      if (end === -1) break;
      const inner = template.slice(i + 1, end);
      const name = inner.split(/[!:]/, 1)[0];
      if (name) names.add(name);
      i = end + 1;
      continue;
    }
    if (char === "}" && template[i + 1] === "}") {
      i += 2;
      continue;
    }
    i += 1;
  }
  return names;
}

function boundsInverted(minimum: number | null, maximum: number | null): boolean {
  return minimum !== null && maximum !== null && minimum > maximum;
}

// Return the first structural problem with the output fields, or null when they all hold.
function outputFieldsError(fields: OutputField[]): string | null {
  if (fields.length === 0) {
    return "Add at least one output field.";
  }

  const seen = new Set<string>();
  for (const f of fields) {
    if (!isValidFieldName(f.name)) {
      return `Field name '${f.name}' is not a valid identifier.`;
    }
    if (seen.has(f.name)) {
      return `Output field names must be unique; '${f.name}' is repeated.`;
    }
    seen.add(f.name);

    if (f.type === "enum") {
      if (!f.enum_values || f.enum_values.length === 0) {
        return `Field '${f.name}' is an enum but has no enum_values.`;
      }
    } else if (f.enum_values !== null) {
      return `Field '${f.name}' sets enum_values but is not an enum.`;
    }

    if (f.minimum !== null || f.maximum !== null) {
      if (!NUMERIC_FIELD_TYPES.has(f.type)) {
        return `Field '${f.name}' sets numeric bounds but is not an int or float.`;
      }
      if (boundsInverted(f.minimum, f.maximum)) {
        return `Field '${f.name}' has minimum ${f.minimum} greater than maximum ${f.maximum}.`;
      }
    }
  }
  return null;
}

function arraysEqual(a: string[] | null, b: string[] | null): boolean {
  if (a === null || b === null) return a === b;
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

/** Validate a draft against the persistence rules; return {} when it would save. */
export function validateVersion(draft: VersionDraft): VersionErrors {
  const errors: VersionErrors = {};

  if (draft.version_name.trim() === "") {
    errors.version_name = "Version name is required.";
  }

  if (draft.model === "") {
    errors.model = "Model is required.";
  }

  const fieldsError = outputFieldsError(draft.output_fields);
  if (fieldsError) {
    errors.output_fields = fieldsError;
  }

  if (draft.required_columns.length === 0) {
    errors.required_columns = "Add at least one required column.";
  }

  // Score-related checks depend on a resolvable score field, so only run them when the
  // output fields are otherwise sound.
  const byName = new Map(draft.output_fields.map((f) => [f.name, f]));
  const score = byName.get(draft.score_field);
  if (!fieldsError) {
    if (!score) {
      errors.score_field = `score_field '${draft.score_field}' does not name an output field.`;
    } else if (draft.score_kind === "categorical") {
      if (score.type !== "enum") {
        errors.score_kind = `Categorical score field '${score.name}' must be an enum, not ${score.type}.`;
      } else if (!arraysEqual(draft.score_labels, score.enum_values)) {
        errors.score_labels = `score_labels must exactly match the score field's enum_values ${JSON.stringify(score.enum_values)}.`;
      }
    } else if (!NUMERIC_FIELD_TYPES.has(score.type)) {
      errors.score_kind = `Numeric score field '${score.name}' must be an int or float, not ${score.type}.`;
    }
  }

  const missing = [...templateColumns(draft.prompt_template)].filter(
    (name) => !draft.required_columns.includes(name),
  );
  if (missing.length > 0) {
    errors.prompt_template = `prompt_template references columns not in required_columns: ${missing.join(", ")}.`;
  }

  return errors;
}
