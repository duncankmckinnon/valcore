import type { ComponentType } from "react";
import { NavLink, Outlet } from "react-router-dom";
import {
  AnnotationIcon,
  CompareIcon,
  DatasetIcon,
  DocsIcon,
  EvaluatorIcon,
  OverviewIcon,
  RunIcon,
  SettingsIcon,
} from "./icons";
import { DOCS_BASE_URL } from "../docsLinks";

type NavItem = { to: string; label: string; Icon: ComponentType<{ className?: string }> };
type NavSection = { label: string; items: NavItem[] };

// Overview and Docs are the two ungrouped entries at the top; the labelled sections
// below them mirror the author-then-measure flow of the product. Docs sits up here
// rather than at the bottom because it is what you read before you have anything to
// author or measure — a new user needs it first, not last.
const OVERVIEW: NavItem = { to: "/", label: "Overview", Icon: OverviewIcon };
const SETTINGS: NavItem = { to: "/settings", label: "Settings", Icon: SettingsIcon };
const SECTIONS: NavSection[] = [
  {
    label: "Author",
    items: [
      { to: "/evaluators", label: "Evaluators", Icon: EvaluatorIcon },
      { to: "/datasets", label: "Datasets", Icon: DatasetIcon },
      { to: "/annotations", label: "Annotations", Icon: AnnotationIcon },
    ],
  },
  {
    label: "Measure",
    items: [
      { to: "/runs", label: "Runs", Icon: RunIcon },
      { to: "/runs/compare", label: "Compare", Icon: CompareIcon },
    ],
  },
];

function NavItemLink({ to, label, Icon, end }: NavItem & { end?: boolean }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
    >
      <span className="nav-icon">
        <Icon />
      </span>
      <span className="nav-label">{label}</span>
    </NavLink>
  );
}

export default function Layout() {
  return (
    <div className="layout">
      <nav className="nav">
        <div className="nav-top">
          <div className="nav-brand-row">
            <span className="nav-logo-stage">
              <img className="nav-logo" src="/logo.png" alt="" />
            </span>
            <span>
              <span className="nav-brand">valcore</span>
              <span className="nav-brand-meta">evaluation workbench</span>
            </span>
          </div>
          <span className="nav-live" title="Local workspace is active">
            <span className="nav-live-dot" /> local
          </span>
        </div>
        {/* `end` keeps the "/" link from matching every route and staying active. */}
        <NavItemLink {...OVERVIEW} end />
        <NavItemLink {...SETTINGS} />
        {SECTIONS.map((section) => (
          <div className="nav-section" key={section.label}>
            <div className="nav-section-label">{section.label}</div>
            {section.items.map((item) => (
              <NavItemLink key={item.to} {...item} />
            ))}
          </div>
        ))}
        <div className="nav-external">
          <a className="nav-link" href={DOCS_BASE_URL} target="_blank" rel="noreferrer">
            <span className="nav-icon"><DocsIcon /></span>
            <span className="nav-label">Docs</span>
          </a>
        </div>
        <div className="nav-footer">
          <span className="nav-footer-dot" />
          <span><strong>Local-first</strong> Your evaluation data stays under your control.</span>
        </div>
      </nav>
      <main className="content-wrap">
        <div className="content-glow" aria-hidden="true" />
        <div className="content">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
