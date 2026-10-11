import { ApiError } from "../lib/api";

export function ErrorNotice({ error, onRetry }: { error: Error | null; onRetry?: () => void }) {
  if (!error) return null;
  const api = error instanceof ApiError ? error : null;
  return (
    <div className="notice notice--danger" role="alert">
      <p className="notice__message">{error.message}</p>
      {api?.details.length ? (
        <ul className="notice__list">
          {api.details.map((issue) => (
            <li key={`${issue.field}:${issue.message}`}>
              <strong>{issue.field}</strong> {issue.message}
            </li>
          ))}
        </ul>
      ) : null}
      <p className="notice__meta">
        {api?.requestId ? <span>Request ID: {api.requestId}</span> : null}
        {api?.retryAfterSeconds ? <span>Retry in {api.retryAfterSeconds} s</span> : null}
        {onRetry ? (
          <button type="button" className="btn btn--ghost btn--sm" onClick={onRetry}>
            Try again
          </button>
        ) : null}
      </p>
    </div>
  );
}
