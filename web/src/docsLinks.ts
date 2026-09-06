export const DOCS_BASE_URL = "https://e-valcore.com/docs";

const LEGACY_DOC_TARGETS: Record<string, string> = {
  keys: "configuration#credentials",
  evals: "evaluators",
  evaluators: "evaluators",
  datasets: "datasets",
  runs: "experiments",
  experiments: "experiments",
  cli: "cli",
};

/** Keep bookmarks from the former embedded docs useful after the website becomes canonical. */
export function websiteDocsUrl(slug?: string): string {
  if (!slug) return `${DOCS_BASE_URL}/getting-started`;
  const target = LEGACY_DOC_TARGETS[slug] ?? "getting-started";
  return `${DOCS_BASE_URL}/${target}`;
}
