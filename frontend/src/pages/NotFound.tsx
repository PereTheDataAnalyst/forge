import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="container" style={{ padding: "96px 24px" }}>
      <h1>Page not found</h1>
      <p className="muted" style={{ margin: "12px 0 24px" }}>
        The page you asked for does not exist or has moved.
      </p>
      <Link to="/" className="btn btn--primary">
        Back to the home page
      </Link>
    </div>
  );
}
