import { describe, expect, it } from "vitest";
import { DOCS_BASE_URL, websiteDocsUrl } from "./docsLinks";

describe("canonical documentation links", () => {
  it("uses the website as the documentation home", () => {
    expect(DOCS_BASE_URL).toBe("https://e-valcore.com/docs");
  });

  it.each([
    [undefined, "getting-started"],
    ["keys", "configuration#credentials"],
    ["evals", "evaluators"],
    ["datasets", "datasets"],
    ["runs", "experiments"],
    ["cli", "cli"],
    ["stale-page", "getting-started"],
  ])("maps the legacy %s page to %s", (legacy, target) => {
    expect(websiteDocsUrl(legacy)).toBe(`${DOCS_BASE_URL}/${target}`);
  });
});
