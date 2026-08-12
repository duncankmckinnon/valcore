// The single source of truth for the docs section: array order is tab order, and the
// tab strip, the routes, and the page titles all read from here. Keeping them in one
// list is what stops a tab existing with no reachable content, or a route existing with
// no tab pointing at it.
//
// Adding a page: write the content component, import it, append an entry.

import type { ComponentType } from "react";
import { Cli } from "./content/Cli";
import { Datasets } from "./content/Datasets";
import { Evals } from "./content/Evals";
import { Keys } from "./content/Keys";
import { Runs } from "./content/Runs";

export type DocEntry = {
  /** URL segment under /docs. Stable — it is what people paste to each other. */
  slug: string;
  /** Tab label, and the page <h1> that DocsPage renders. */
  title: string;
  /** The prose. Takes no props: docs render from static content, never from fetches. */
  Body: ComponentType;
};

export const DOCS: DocEntry[] = [
  // Keys leads, and so is what /docs lands on: nothing that calls a model runs until
  // the gateway key exists, which makes it the first thing a new user needs.
  { slug: "keys", title: "Keys", Body: Keys },
  { slug: "evals", title: "Evals", Body: Evals },
  { slug: "datasets", title: "Datasets", Body: Datasets },
  { slug: "runs", title: "Runs", Body: Runs },
  { slug: "cli", title: "CLI", Body: Cli },
];

// Resolving in code rather than redirecting: an unknown slug renders the first tab and
// leaves the URL alone, so a stale link degrades to something readable instead of a
// blank pane, and no history entry is spent on the correction. The bare /docs route
// arrives here as `undefined` and lands in the same place.
export function resolveDoc(slug: string | undefined): DocEntry {
  return DOCS.find((entry) => entry.slug === slug) ?? DOCS[0];
}
