import { describe, expect, it } from "vitest";
import type { OutputField } from "../api/types";
import { validateVersion, type VersionDraft } from "./versionValidation";

// A field factory keeps each case focused on the one attribute under test.
function field(overrides: Partial<OutputField> = {}): OutputField {
  return {
    name: "verdict",
    type: "enum",
    description: "",
    required: true,
    enum_values: ["pass", "fail"],
    minimum: null,
    maximum: null,
    ...overrides,
  };
}

// A fully valid categorical draft: the score field is an enum whose values the
// score_labels mirror exactly.
function categoricalDraft(overrides: Partial<VersionDraft> = {}): VersionDraft {
  return {
    version_name: "v1",
    model: "openai:gpt-4o",
    instructions: "Judge the answer.",
    prompt_template: "{question}",
    required_columns: ["question"],
    output_fields: [field()],
    score_field: "verdict",
    score_kind: "categorical",
    score_labels: ["pass", "fail"],
    score_minimum: null,
    score_maximum: null,
    capabilities: [],
    tools: [],
    ...overrides,
  };
}

// A fully valid numeric draft: the score field is a bounded float.
function numericDraft(overrides: Partial<VersionDraft> = {}): VersionDraft {
  return {
    version_name: "v1",
    model: "openai:gpt-4o",
    instructions: "Rate the answer.",
    prompt_template: "{answer}",
    required_columns: ["answer"],
    output_fields: [
      field({ name: "rating", type: "float", enum_values: null, minimum: 0, maximum: 1 }),
    ],
    score_field: "rating",
    score_kind: "numeric",
    score_labels: null,
    score_minimum: 0,
    score_maximum: 1,
    capabilities: [],
    tools: [],
    ...overrides,
  };
}

describe("validateVersion", () => {
  it("returns {} for a fully valid categorical draft", () => {
    expect(validateVersion(categoricalDraft())).toEqual({});
  });

  it("returns {} for a fully valid numeric draft", () => {
    expect(validateVersion(numericDraft())).toEqual({});
  });

  describe("version_name", () => {
    it("flags an empty name", () => {
      expect(validateVersion(categoricalDraft({ version_name: "" }))).toHaveProperty(
        "version_name",
      );
    });

    it("flags a whitespace-only name", () => {
      expect(validateVersion(categoricalDraft({ version_name: "   " }))).toHaveProperty(
        "version_name",
      );
    });
  });

  describe("model", () => {
    it("flags an empty model", () => {
      expect(validateVersion(categoricalDraft({ model: "" }))).toHaveProperty("model");
    });
  });

  describe("output_fields", () => {
    it("flags empty output_fields", () => {
      const errors = validateVersion(categoricalDraft({ output_fields: [] }));
      expect(errors).toHaveProperty("output_fields");
    });

    it("flags duplicate field names", () => {
      const errors = validateVersion(
        categoricalDraft({
          output_fields: [field(), field()],
        }),
      );
      expect(errors).toHaveProperty("output_fields");
    });

    it("flags a field whose name starts with a digit", () => {
      const errors = validateVersion(
        categoricalDraft({
          output_fields: [field({ name: "2bad" })],
          score_field: "2bad",
        }),
      );
      expect(errors).toHaveProperty("output_fields");
    });

    it("flags a field named after a reserved word", () => {
      const errors = validateVersion(
        categoricalDraft({
          output_fields: [field({ name: "class" })],
          score_field: "class",
        }),
      );
      expect(errors).toHaveProperty("output_fields");
    });

    it("flags an enum field with no enum_values", () => {
      const errors = validateVersion(
        categoricalDraft({
          output_fields: [field({ enum_values: [] })],
        }),
      );
      expect(errors).toHaveProperty("output_fields");
    });

    it("flags an enum field with null enum_values", () => {
      const errors = validateVersion(
        categoricalDraft({
          output_fields: [field({ enum_values: null })],
        }),
      );
      expect(errors).toHaveProperty("output_fields");
    });

    it("flags a non-enum field that sets enum_values", () => {
      const errors = validateVersion(
        numericDraft({
          output_fields: [
            field({ name: "rating", type: "float", enum_values: ["x"], minimum: null, maximum: null }),
          ],
        }),
      );
      expect(errors).toHaveProperty("output_fields");
    });

    it("flags numeric bounds on a non-numeric field", () => {
      const errors = validateVersion(
        categoricalDraft({
          output_fields: [field({ minimum: 0 })],
        }),
      );
      expect(errors).toHaveProperty("output_fields");
    });

    it("flags minimum greater than maximum", () => {
      const errors = validateVersion(
        numericDraft({
          output_fields: [
            field({ name: "rating", type: "float", enum_values: null, minimum: 5, maximum: 1 }),
          ],
        }),
      );
      expect(errors).toHaveProperty("output_fields");
    });
  });

  describe("required_columns", () => {
    it("flags empty required_columns", () => {
      // Drop the placeholder too so prompt_template does not also complain.
      const errors = validateVersion(
        categoricalDraft({ required_columns: [], prompt_template: "" }),
      );
      expect(errors).toHaveProperty("required_columns");
    });
  });

  describe("score_field", () => {
    it("flags a score_field that names no output field", () => {
      const errors = validateVersion(categoricalDraft({ score_field: "missing" }));
      expect(errors).toHaveProperty("score_field");
      expect(errors.score_field).toContain("missing");
    });
  });

  describe("score_kind", () => {
    it("flags categorical kind over a str score field", () => {
      const errors = validateVersion(
        categoricalDraft({
          output_fields: [field({ type: "str", enum_values: null })],
          score_labels: null,
        }),
      );
      expect(errors).toHaveProperty("score_kind");
    });

    it("flags numeric kind over an enum score field", () => {
      const errors = validateVersion(
        numericDraft({
          output_fields: [field({ name: "rating", type: "enum", enum_values: ["a", "b"] })],
        }),
      );
      expect(errors).toHaveProperty("score_kind");
    });
  });

  describe("score_labels", () => {
    it("flags labels that differ from the score field's enum_values", () => {
      const errors = validateVersion(categoricalDraft({ score_labels: ["pass", "wrong"] }));
      expect(errors).toHaveProperty("score_labels");
    });

    it("flags labels in a different order than enum_values", () => {
      const errors = validateVersion(categoricalDraft({ score_labels: ["fail", "pass"] }));
      expect(errors).toHaveProperty("score_labels");
    });
  });

  describe("prompt_template", () => {
    it("flags a placeholder missing from required_columns and names it", () => {
      const errors = validateVersion(
        categoricalDraft({
          prompt_template: "{question} {missing}",
          required_columns: ["question"],
        }),
      );
      expect(errors).toHaveProperty("prompt_template");
      expect(errors.prompt_template).toContain("missing");
    });

    it("does not flag doubled braces as a placeholder", () => {
      const errors = validateVersion(
        categoricalDraft({
          prompt_template: "{{literal}}",
          required_columns: ["question"],
        }),
      );
      expect(errors.prompt_template).toBeUndefined();
    });

    it("ignores an empty {} placeholder", () => {
      const errors = validateVersion(
        categoricalDraft({
          prompt_template: "{question} {}",
          required_columns: ["question"],
        }),
      );
      expect(errors.prompt_template).toBeUndefined();
    });

    it("strips conversion and format specs when matching a placeholder", () => {
      const errors = validateVersion(
        categoricalDraft({
          prompt_template: "{question!r} {question:>10}",
          required_columns: ["question"],
        }),
      );
      expect(errors.prompt_template).toBeUndefined();
    });
  });

  // Supplementary cases beyond the plan that exercise the mirror's edge logic.
  describe("edge cases", () => {
    it("flags null score_labels for a categorical enum score field", () => {
      // Python compares `score_labels != enum_values`; null never equals the value list.
      const errors = validateVersion(categoricalDraft({ score_labels: null }));
      expect(errors).toHaveProperty("score_labels");
    });

    it("returns {} for a valid int-typed numeric score field", () => {
      const errors = validateVersion(
        numericDraft({
          output_fields: [
            field({ name: "rating", type: "int", enum_values: null, minimum: 1, maximum: 5 }),
          ],
        }),
      );
      expect(errors).toEqual({});
    });

    it("accumulates independent errors across fields", () => {
      const errors = validateVersion(
        categoricalDraft({ version_name: "", model: "", required_columns: [], prompt_template: "" }),
      );
      expect(errors).toHaveProperty("version_name");
      expect(errors).toHaveProperty("model");
      expect(errors).toHaveProperty("required_columns");
    });

    it("does not run score checks while output_fields are invalid", () => {
      // With empty output_fields, score_field cannot resolve, but the plan keys the problem
      // to output_fields alone rather than doubling up on score_field.
      const errors = validateVersion(categoricalDraft({ output_fields: [] }));
      expect(errors).toHaveProperty("output_fields");
      expect(errors.score_field).toBeUndefined();
    });
  });
});
