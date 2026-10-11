import { Link, Outlet } from "react-router-dom";
import { BrandLink } from "../components/Logo";

export function AuthLayout() {
  return (
    <div className="auth-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <div className="auth-shell__brand">
        <BrandLink />
      </div>
      <main id="main" className="auth-card">
        <Outlet />
      </main>
      <p className="auth-shell__back muted">
        <Link to="/">Back to the website</Link>
      </p>
    </div>
  );
}
