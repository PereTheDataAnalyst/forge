import { Navigate, Outlet, useLocation } from "react-router-dom";
import { Spinner } from "../components/Spinner";
import { useAuth } from "./AuthContext";

export function RequireAuth() {
  const { status } = useAuth();
  const location = useLocation();
  if (status === "loading") {
    return (
      <div className="fullpage-center">
        <Spinner label="Checking your session" />
      </div>
    );
  }
  if (status === "anonymous") {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  return <Outlet />;
}
