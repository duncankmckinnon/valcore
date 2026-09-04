// Logfire UI URLs for the reference project. List and workbench links come from
// setup status; a dataset's cases page is the evals URL plus its hosted name.

export function datasetCasesUrl(evalsUrl: string, name: string): string {
  return `${evalsUrl.replace(/\/$/, "")}/${encodeURIComponent(name)}/cases`;
}
