import { useState } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { BrandLink } from "../components/Logo";
import { extensionNavItems, type NavItem } from "../extensions/registry";

const coreNav: NavItem[] = [
  { to: "/app", label: "Dashboard", end: true },
  { to: "/app/projects", label: "Projects" },
];

export function AppLayout() {
  const { user, logout } = useAuth();
  const [signingOut, setSigningOut] = useState(false);
  const items = [...coreNav, ...extensionNavItems];

  async function handleSignOut() {
    setSigningOut(true);
    try {
      await logout();
    } finally {
      setSigningOut(false);
    }
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <div className="sidebar__brand">
          <BrandLink to="/app" />
        </div>
        <nav className="sidebar__nav" aria-label="Application">
          {items.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => `nav-item${isActive ? " nav-item--active" : ""}`}
            >
              {item.label}
            </NavLink>
          ))}
          <Link to="/docs" className="nav-item">
            Documentation
          </Link>
        </nav>
        <div className="sidebar__user">
          <p className="sidebar__email" title={user?.email}>
            {user?.email}
          </p>
          <button
            type="button"
            className="btn btn--secondary btn--sm"
            onClick={handleSignOut}
            disabled={signingOut}
          >
            {signingOut ? "Signing out" : "Sign out"}
          </button>
        </div>
      </aside>
      <main id="main" className="app-main">
        <div className="app-main__inner">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
