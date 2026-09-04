import { describe, expect, it } from "vitest";
import { datasetCasesUrl } from "./logfireLinks";

describe("datasetCasesUrl", () => {
  it("appends the dataset name and /cases under the evals URL", () => {
    expect(
      datasetCasesUrl(
        "https://logfire-us.pydantic.dev/duncan/agent-tracing/evals",
        "agent_responses",
      ),
    ).toBe("https://logfire-us.pydantic.dev/duncan/agent-tracing/evals/agent_responses/cases");
  });

  it("encodes a name that is not a bare path segment", () => {
    expect(
      datasetCasesUrl("https://logfire-us.pydantic.dev/duncan/agent-tracing/evals", "My set"),
    ).toBe("https://logfire-us.pydantic.dev/duncan/agent-tracing/evals/My%20set/cases");
  });
});
