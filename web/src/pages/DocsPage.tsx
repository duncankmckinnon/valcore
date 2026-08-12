// The docs surface: one route component behind both /docs and /docs/:slug. It owns the
// tab strip, the page heading, and slug resolution — and knows nothing about the prose,
// which lives in web/src/docs/content and reaches it through the registry.

import { Link, useParams } from "react-router-dom";
import { PageHeader } from "../components/PageHeader";
import { DOCS, resolveDoc } from "../docs/registry";

// Plain Links rather than NavLinks, with the current tab passed in. NavLink derives
// active state by matching the URL, which is wrong for the two routes that fall back:
// /docs and /docs/<unknown> both render the first tab's body, but neither URL matches
// /docs/evals, so the strip would sit entirely unlit above real content. The selected
// tab is by definition the entry whose body is rendered, so that is what decides it.
//
// Links, not buttons: tabs are locations people middle-click, copy, and land on.
function DocsTabs({ currentSlug }: { currentSlug: string }): JSX.Element {
  return (
    <nav className="docs-tabs">
      {DOCS.map((entry) => {
        const isCurrent = entry.slug === currentSlug;
        return (
          <Link
            key={entry.slug}
            to={`/docs/${entry.slug}`}
            className={isCurrent ? "docs-tab active" : "docs-tab"}
            // Carries the selection to assistive tech, which the colour alone does not.
            aria-current={isCurrent ? "page" : undefined}
          >
            {entry.title}
          </Link>
        );
      })}
    </nav>
  );
}

export default function DocsPage(): JSX.Element {
  const { slug } = useParams();
  const entry = resolveDoc(slug);
  const { Body } = entry;

  return (
    <>
      <PageHeader
        title={entry.title}
        description="How valcore works, from the app you are looking at."
      />
      <DocsTabs currentSlug={entry.slug} />
      <Body />
    </>
  );
}
