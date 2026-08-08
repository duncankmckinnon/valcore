import { describe, expect, it } from "vitest";
import { parseFile } from "./parse";

// A pydantic_evals dataset: a bare object with a `cases` array. The disambiguator must
// recognize it by shape alone, with no valcore-specific marker present.
const DATASET_JSON = JSON.stringify({
  name: "refusal-quality",
  evaluators: [{ ValcoreJudge: { package: "refusal_quality.json" } }],
  cases: [
    {
      name: "row-1",
      inputs: { question: "Q1", answer: "A1" },
      expected_output: "refusal",
      metadata: null,
    },
    {
      name: "row-2",
      inputs: { question: "Q2", answer: "A2" },
      expected_output: "answer",
      metadata: null,
    },
  ],
});

// A bundled valcore eval package: the dataset section is nested under `dataset`, and the
// document is marked by `kind`. Columns come from the nested `dataset.cases`.
const PACKAGE_JSON = JSON.stringify({
  kind: "valcore/eval-package",
  version: 1,
  agent: {
    model: "openai:gpt-4o",
    name: "Refusal Judge",
    instructions: "You judge whether an answer is a refusal.",
    output_schema: { type: "object", properties: { score: { type: "string" } }, required: ["score"] },
  },
  valcore: {
    prompt_template: "Q: {question}\nA: {answer}",
    required_columns: ["question", "answer"],
    score_field: "score",
    score_kind: "categorical",
    score_labels: ["refusal", "partial", "answer"],
    tools: [],
  },
  dataset: {
    name: "refusal-quality",
    evaluators: [{ ValcoreJudge: { package: "refusal_quality.json" } }],
    cases: [
      {
        name: "row-1",
        inputs: { question: "Q1", answer: "A1" },
        expected_output: "refusal",
        metadata: null,
      },
      {
        name: "row-2",
        inputs: { question: "Q2", answer: "A2" },
        expected_output: "answer",
        metadata: null,
      },
    ],
  },
});

describe("parseFile — CSV", () => {
  it("parses a CSV into inferred columns and rows, tagged kind csv", () => {
    const result = parseFile("data.csv", "text,label\nhello,good\nworld,bad\n");

    expect(result.kind).toBe("csv");
    expect(result.columns).toEqual(["text", "label"]);
    expect(result.rows).toEqual([
      { text: "hello", label: "good" },
      { text: "world", label: "bad" },
    ]);
  });
});

describe("parseFile — JSONL", () => {
  // The fall-through regression guard: a genuine JSONL file is not valid JSON as a whole,
  // so it must still be handled by the unchanged JSONL parser and reported as kind jsonl.
  it("parses a JSONL file exactly as before, tagged kind jsonl", () => {
    const result = parseFile(
      "data.jsonl",
      '{"text": "hello", "label": "good"}\n{"text": "world", "label": "bad"}\n',
    );

    expect(result.kind).toBe("jsonl");
    expect(result.columns).toEqual(["text", "label"]);
    expect(result.rows).toEqual([
      { text: "hello", label: "good" },
      { text: "world", label: "bad" },
    ]);
  });

  it("stringifies non-string JSONL values, unchanged from today", () => {
    const result = parseFile("data.jsonl", '{"text": "hi", "meta": {"n": 1}}\n');

    expect(result.kind).toBe("jsonl");
    expect(result.rows[0]).toEqual({ text: "hi", meta: '{"n":1}' });
  });
});

describe("parseFile — pydantic_evals dataset (.json)", () => {
  it("recognizes a bare dataset by its cases array and tags it kind package", () => {
    const result = parseFile("refusal.json", DATASET_JSON);

    expect(result.kind).toBe("package");
  });

  it("derives columns and rows from cases[].inputs so it previews like a CSV", () => {
    const result = parseFile("refusal.json", DATASET_JSON);

    expect(result.columns).toEqual(["question", "answer"]);
    expect(result.rows).toEqual([
      { question: "Q1", answer: "A1" },
      { question: "Q2", answer: "A2" },
    ]);
  });

  it("takes the first-seen union of inputs keys across cases", () => {
    const json = JSON.stringify({
      name: "mixed",
      cases: [
        { name: "a", inputs: { question: "Q", answer: "A" } },
        { name: "b", inputs: { question: "Q2", answer: "A2", source: "S" } },
      ],
    });

    const result = parseFile("mixed.json", json);

    expect(result.kind).toBe("package");
    expect(result.columns).toEqual(["question", "answer", "source"]);
  });
});

describe("parseFile — bundled eval package (.json)", () => {
  it("recognizes the package by kind and reads its nested dataset.cases", () => {
    const result = parseFile("refusal_quality.json", PACKAGE_JSON);

    expect(result.kind).toBe("package");
    expect(result.columns).toEqual(["question", "answer"]);
    expect(result.rows).toEqual([
      { question: "Q1", answer: "A1" },
      { question: "Q2", answer: "A2" },
    ]);
  });
});

describe("parseFile — malformed .json falls through", () => {
  // A .json file that is not valid JSON as a whole (here, line-delimited JSON) must not
  // throw: the disambiguator's JSON.parse fails and the unchanged JSONL parser takes over,
  // leaving the server to decide authoritatively.
  it("does not throw and falls through to the JSONL parser", () => {
    const body = '{"question": "hi"}\n{"question": "bye"}\n';

    expect(() => parseFile("weird.json", body)).not.toThrow();
    const result = parseFile("weird.json", body);
    expect(result.kind).toBe("jsonl");
    expect(result.columns).toEqual(["question"]);
    expect(result.rows).toEqual([{ question: "hi" }, { question: "bye" }]);
  });

  it("falls through when the JSON parses but the shape is unrecognized", () => {
    // A valid single-object JSON with neither a cases array nor the package kind is not a
    // package; it is handled by the JSONL parser (which reads the one object as one row).
    const result = parseFile("plain.json", '{"foo": "bar"}');

    expect(result.kind).toBe("jsonl");
    expect(result.columns).toEqual(["foo"]);
    expect(result.rows).toEqual([{ foo: "bar" }]);
  });
});
