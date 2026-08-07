import type { ComponentType } from "react";
import { NavLink, Outlet } from "react-router-dom";
import {
  CompareIcon,
  DatasetIcon,
  EvaluatorIcon,
  OverviewIcon,
  RunIcon,
} from "./icons";

type NavItem = { to: string; label: string; Icon: ComponentType<{ className?: string }> };
type NavSection = { label: string; items: NavItem[] };

// Overview stands alone above the labelled groups; the two sections mirror the
// author-then-measure flow of the product.
const OVERVIEW: NavItem = { to: "/", label: "Overview", Icon: OverviewIcon };
const SECTIONS: NavSection[] = [
  {
    label: "Author",
    items: [
      { to: "/evaluators", label: "Evaluators", Icon: EvaluatorIcon },
      { to: "/datasets", label: "Datasets", Icon: DatasetIcon },
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
      {label}
    </NavLink>
  );
}

export default function Layout() {
  return (
    <div className="layout">
      <nav className="nav">
        <div className="nav-brand-row">
          <img className="nav-logo" src="/logo.png" alt="" />
          <span className="nav-brand">valcore</span>
        </div>
        {/* `end` keeps the "/" link from matching every route and staying active. */}
        <NavItemLink {...OVERVIEW} end />
        {SECTIONS.map((section) => (
          <div key={section.label}>
            <div className="nav-section-label">{section.label}</div>
            {section.items.map((item) => (
              <NavItemLink key={item.to} {...item} />
            ))}
          </div>
        ))}
      </nav>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
