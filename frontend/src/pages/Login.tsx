import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ErrorNotice } from "../components/ErrorNotice";
import { useDocumentTitle } from "../lib/useDocumentTitle";

function safeNext(value: string | null): string {
  return value && value.startsWith("/app") && !value.startsWith("//") ? value : "/app";
}

export function LoginPage() {
  useDocumentTitle("Sign in");
  const { status, login } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  if (status === "authenticated") return <Navigate to={safeNext(params.get("next"))} replace />;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
      navigate(safeNext(params.get("next")), { replace: true });
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error("Sign in failed."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Sign in</h1>
      <p className="auth-card__lead">Use the account you created for N-ATLAS Forge.</p>
      <form className="form" onSubmit={onSubmit} noValidate>
        <ErrorNotice error={error} />
        <div className="field">
          <label className="field__label" htmlFor="email">
            Email
          </label>
          <input
            id="email"
            className="input"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </div>
        <div className="field">
          <label className="field__label" htmlFor="password">
            Password
          </label>
          <input
            id="password"
            className="input"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>
        <button type="submit" className="btn btn--primary btn--lg" disabled={busy || !email || !password}>
          {busy ? "Signing in" : "Sign in"}
        </button>
      </form>
      <p className="auth-card__switch">
        New to Forge? <Link to="/register">Create an account</Link>
      </p>
    </>
  );
}
