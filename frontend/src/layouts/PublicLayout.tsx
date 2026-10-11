import { Link, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { BrandLink } from "../components/Logo";
import { contactHref, site } from "../config";

export function PublicLayout() {
  const { status } = useAuth();
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="site-header">
        <div className="container site-header__inner">
          <BrandLink />
          <nav className="site-nav" aria-label="Primary">
            <a href="/#platform">Platform</a>
            <a href="/#languages">Languages</a>
            <Link to="/docs">Documentation</Link>
            <Link to="/security">Security</Link>
          </nav>
          <div className="site-header__actions">
            {status === "authenticated" ? (
              <Link to="/app" className="btn btn--primary btn--sm">
                Open dashboard
              </Link>
            ) : (
              <>
                <Link to="/login" className="btn btn--ghost btn--sm">
                  Sign in
                </Link>
                <Link to="/register" className="btn btn--primary btn--sm">
                  Start building
                </Link>
              </>
            )}
          </div>
        </div>
      </header>

      <main id="main">
        <Outlet />
      </main>

      <footer className="site-footer">
        <div className="container">
          <div className="site-footer__grid">
            <div>
              <p className="site-footer__brand">N-ATLAS Forge</p>
              <p className="site-footer__tagline">
                Developer infrastructure for building with N-ATLAS. Built by {site.company}.
              </p>
            </div>
            <nav aria-label="Product">
              <h2 className="site-footer__heading">Product</h2>
              <ul>
                <li>
                  <a href="/#platform">Platform</a>
                </li>
                <li>
                  <a href="/#languages">Languages</a>
                </li>
                <li>
                  <Link to="/docs">Documentation</Link>
                </li>
                <li>
                  <a href={site.repoUrl} rel="noreferrer">
                    GitHub
                  </a>
                </li>
              </ul>
            </nav>
            <nav aria-label="Company">
              <h2 className="site-footer__heading">{site.company}</h2>
              <ul>
                <li>
                  <a href={contactHref} rel="noreferrer">
                    Contact
                  </a>
                </li>
                <li>
                  <a href={site.challengeUrl} rel="noreferrer">
                    National AI Innovation Challenge
                  </a>
                </li>
              </ul>
            </nav>
            <nav aria-label="Legal">
              <h2 className="site-footer__heading">Legal</h2>
              <ul>
                <li>
                  <Link to="/security">Security</Link>
                </li>
                <li>
                  <Link to="/privacy">Privacy</Link>
                </li>
                <li>
                  <Link to="/terms">Terms</Link>
                </li>
              </ul>
            </nav>
          </div>
          <div className="site-footer__legal">
            <p>{site.attribution}</p>
            <p>
              N-ATLAS Forge is an independent project by {site.company}. It is not an official
              product of the Federal Ministry of Communications, Innovation and Digital Economy,
              NCAIR, NITDA or Awarri Technologies, and it is not endorsed by them.
            </p>
            <p>&copy; {new Date().getFullYear()} {site.company}</p>
          </div>
        </div>
      </footer>
    </>
  );
}
