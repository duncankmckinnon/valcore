import { NavLink, Outlet } from "react-router-dom";

const NAV = [
  { to: "/evaluators", label: "Evaluators" },
  { to: "/datasets", label: "Datasets" },
  { to: "/runs", label: "Runs" },
];

export default function Layout() {
  return (
    <div className="layout">
      <nav className="nav">
        <div className="nav-brand">eval-core</div>
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
