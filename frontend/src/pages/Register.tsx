import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ErrorNotice } from "../components/ErrorNotice";
import { site } from "../config";
import { ApiError } from "../lib/api";
import { useDocumentTitle } from "../lib/useDocumentTitle";

function fieldMessage(error: Error | null, field: string): string | null {
  if (!(error instanceof ApiError)) return null;
  return error.details.find((issue) => issue.field === field)?.message ?? null;
}

export function RegisterPage() {
  useDocumentTitle("Create your account");
  const { status, register } = useAuth();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  if (status === "authenticated") return <Navigate to="/app" replace />;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await register(email.trim(), password, name.trim() || undefined);
      navigate("/app", { replace: true });
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error("Could not create the account."));
    } finally {
      setBusy(false);
    }
  }

  const passwordIssue = fieldMessage(error, "password");

  return (
    <>
      <h1>Create your account</h1>
      <p className="auth-card__lead">Start building with N-ATLAS in a few minutes.</p>
      <form className="form" onSubmit={onSubmit} noValidate>
        <ErrorNotice error={error} />
        <div className="field">
          <label className="field__label" htmlFor="name">
            Name <span className="muted">(optional)</span>
          </label>
          <input
            id="name"
            className="input"
            autoComplete="name"
            maxLength={120}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
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
            aria-invalid={fieldMessage(error, "email") ? "true" : undefined}
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
            autoComplete="new-password"
            required
            minLength={10}
            maxLength={128}
            aria-describedby="password-hint"
            aria-invalid={passwordIssue ? "true" : undefined}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <span id="password-hint" className="field__hint">
            At least 10 characters. Avoid common passwords and your email address.
          </span>
          {passwordIssue ? <span className="field__error">{passwordIssue}</span> : null}
        </div>
        <button
          type="submit"
          className="btn btn--primary btn--lg"
          disabled={busy || !email || password.length < 1}
        >
          {busy ? "Creating account" : "Create account"}
        </button>
        <p className="field__hint">
          By creating an account you agree to the <Link to="/terms">Terms</Link> and acknowledge
          that use of the model is subject to the{" "}
          <a href={site.modelCardUrl} rel="noreferrer">
            N-ATLaS Terms of Use
          </a>
          .
        </p>
      </form>
      <p className="auth-card__switch">
        Already have an account? <Link to="/login">Sign in</Link>
      </p>
    </>
  );
}
