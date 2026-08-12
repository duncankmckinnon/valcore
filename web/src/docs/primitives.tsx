// The vocabulary every docs content file is written in. Content files import only
// these, so prose never carries a class name or a styling decision — the same split
// that keeps PageHeader the sole owner of the page <h1>.

import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Button } from "../components/ui";
import { InfoIcon } from "../components/icons";

// Vertical rhythm wrapper for one tab's body. Sections inside it are plain <section>
// elements with their own heading, so the document outline stays flat under the
// PageHeader <h1> that DocsPage owns.
export function DocPage({ children }: { children: ReactNode }): JSX.Element {
  return <div className="doc-body">{children}</div>;
}

// One titled block of prose. The heading is an <h2> because the tab title is the
// page's <h1>, owned by PageHeader in DocsPage.
export function DocSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <section className="doc-section">
      <h2>{title}</h2>
      {children}
    </section>
  );
}

// A copyable command. `children` is the literal command string rather than markup so
// the clipboard write and the rendered text can never diverge.
export function CodeBlock({ children }: { children: string }): JSX.Element {
  const copy = () => {
    void navigator.clipboard.writeText(children);
  };

  return (
    <div className="doc-code">
      <code>{children}</code>
      <Button variant="secondary" onClick={copy}>
        Copy
      </Button>
    </div>
  );
}

// An aside for a caveat worth interrupting the prose for. The icon is decorative —
// the text carries the meaning.
export function DocNote({ children }: { children: ReactNode }): JSX.Element {
  return (
    <aside className="doc-note">
      <InfoIcon />
      <div>{children}</div>
    </aside>
  );
}

// A link to somewhere else in the app — another docs tab or a working surface. Wraps
// react-router's Link so docs navigation stays client-side and never reloads the SPA.
export function DocLink({ to, children }: { to: string; children: ReactNode }): JSX.Element {
  return (
    <Link className="doc-link" to={to}>
      {children}
    </Link>
  );
}

// A link off the app entirely — provider dashboards and upstream docs. A plain anchor
// rather than a router Link, which would treat the URL as an in-app path. Opens in a new
// tab: this is a local workspace with unsaved editor state, and navigating away in the
// same tab would discard it.
export function ExternalLink({
  href,
  children,
}: {
  href: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <a className="doc-link" href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  );
}
