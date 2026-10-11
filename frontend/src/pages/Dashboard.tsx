import { Link } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { ErrorNotice } from "../components/ErrorNotice";
import { PageHeader } from "../components/PageHeader";
import { Spinner } from "../components/Spinner";
import { StatusPill, type Tone } from "../components/StatusPill";
import { api } from "../lib/api";
import { formatDateTime, formatLatency, formatNumber, formatRelative } from "../lib/format";
import type { DashboardSummary, ProviderStatus } from "../lib/types";
import { useAsyncData } from "../lib/useAsyncData";
import { useDocumentTitle } from "../lib/useDocumentTitle";

const PROVIDER_LABEL: Record<ProviderStatus["status"], { tone: Tone; label: string }> = {
  ok: { tone: "success", label: "Connected" },
  loading: { tone: "warning", label: "Model loading" },
  not_configured: { tone: "neutral", label: "Not connected" },
  unreachable: { tone: "danger", label: "Unreachable" },
  error: { tone: "danger", label: "Error" },
};

function ProviderPanel({ provider }: { provider: ProviderStatus }) {
  const { tone, label } = PROVIDER_LABEL[provider.status];
  return (
    <section className="panel" aria-labelledby="provider-title">
      <div className="panel__header">
        <h2 id="provider-title" className="panel__title">
          N-ATLAS inference
        </h2>
        <StatusPill tone={tone}>{label}</StatusPill>
      </div>
      <div className="panel__body">
        <dl className="kv">
          <dt>Model</dt>
          <dd className="mono">{provider.model_id ?? "Unknown"}</dd>
          {provider.detail ? (
            <>
              <dt>Detail</dt>
              <dd>{provider.detail}</dd>
            </>
          ) : null}
        </dl>
        {provider.status !== "ok" ? (
          <p className="muted" style={{ marginTop: 12 }}>
            Requests to the gateway return a structured error until the inference worker is
            connected. See{" "}
            <Link to="/docs#connect-n-atlas">how to connect N-ATLAS</Link>.
          </p>
        ) : null}
      </div>
    </section>
  );
}

function Stats({ summary }: { summary: DashboardSummary }) {
  const { totals } = summary;
  return (
    <div className="stats">
      <div className="stat">
        <div className="stat__label">Projects</div>
        <div className="stat__value">{formatNumber(totals.projects)}</div>
      </div>
      <div className="stat">
        <div className="stat__label">API requests</div>
        <div className="stat__value">{formatNumber(totals.requests)}</div>
        <div className="stat__note">Last {summary.window_days} days</div>
      </div>
      <div className="stat">
        <div className="stat__label">Successful</div>
        <div className="stat__value">{formatNumber(totals.successful)}</div>
      </div>
      <div className="stat">
        <div className="stat__label">Failed</div>
        <div className="stat__value">{formatNumber(totals.failed)}</div>
      </div>
      <div className="stat">
        <div className="stat__label">Average latency</div>
        <div className="stat__value">{formatLatency(totals.average_latency_ms)}</div>
        <div className="stat__note">Successful requests only</div>
      </div>
    </div>
  );
}

function Onboarding() {
  return (
    <section className="panel" aria-labelledby="onboarding-title">
      <div className="panel__header">
        <h2 id="onboarding-title" className="panel__title">
          Get started
        </h2>
      </div>
      <div className="panel__body">
        <ol className="steps">
          <li>
            <strong>Create a project.</strong> Projects group API keys, usage and logs.
          </li>
          <li>
            <strong>Generate an API key.</strong> The full key is shown once, so store it safely.
          </li>
          <li>
            <strong>Call N-ATLAS.</strong> Send a request to <code>/api/v1/chat</code> and watch it
            appear here.
          </li>
        </ol>
        <div style={{ marginTop: 16 }}>
          <Link to="/app/projects" className="btn btn--primary">
            Create your first project
          </Link>
        </div>
      </div>
    </section>
  );
}

function RecentRequests({ summary }: { summary: DashboardSummary }) {
  return (
    <section className="panel" aria-labelledby="recent-title">
      <div className="panel__header">
        <h2 id="recent-title" className="panel__title">
          Recent requests
        </h2>
      </div>
      {summary.recent_requests.length === 0 ? (
        <div className="panel__body">
          <EmptyState title="No requests yet">
            Requests made with a project API key appear here with status, latency, model and token
            counts. Prompts and responses are not stored.
          </EmptyState>
        </div>
      ) : (
        <div className="panel__body panel__body--flush table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Project</th>
                <th>Endpoint</th>
                <th>Result</th>
                <th className="num">Latency</th>
                <th className="num">Tokens</th>
              </tr>
            </thead>
            <tbody>
              {summary.recent_requests.map((row) => (
                <tr key={row.id}>
                  <td title={formatDateTime(row.created_at)}>{formatRelative(row.created_at)}</td>
                  <td>{row.project_name}</td>
                  <td className="mono">
                    {row.method} {row.endpoint}
                  </td>
                  <td>
                    {row.outcome === "success" ? (
                      <StatusPill tone="success">{row.status_code}</StatusPill>
                    ) : (
                      <StatusPill tone="danger">
                        {row.status_code} {row.error_code ?? "error"}
                      </StatusPill>
                    )}
                  </td>
                  <td className="num">{formatLatency(row.latency_ms)}</td>
                  <td className="num">
                    {row.prompt_tokens !== null && row.completion_tokens !== null
                      ? formatNumber(row.prompt_tokens + row.completion_tokens)
                      : "None"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function KeysPanel({ summary }: { summary: DashboardSummary }) {
  return (
    <section className="panel" aria-labelledby="keys-title">
      <div className="panel__header">
        <h2 id="keys-title" className="panel__title">
          Active API keys
        </h2>
        <Link to="/app/projects" className="btn btn--ghost btn--sm">
          Manage
        </Link>
      </div>
      {summary.recent_api_keys.length === 0 ? (
        <div className="panel__body">
          <EmptyState title="No active keys">
            Create a project, then generate a key to call the gateway from your application.
          </EmptyState>
        </div>
      ) : (
        <div className="panel__body panel__body--flush table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Key</th>
                <th>Project</th>
                <th>Last used</th>
              </tr>
            </thead>
            <tbody>
              {summary.recent_api_keys.map((key) => (
                <tr key={key.id}>
                  <td>{key.name}</td>
                  <td className="mono">{key.prefix}…</td>
                  <td>
                    <Link to={`/app/projects/${key.project_id}`}>{key.project_name}</Link>
                  </td>
                  <td>{formatRelative(key.last_used_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function EvaluationPanel({ summary }: { summary: DashboardSummary }) {
  return (
    <section className="panel" aria-labelledby="eval-title">
      <div className="panel__header">
        <h2 id="eval-title" className="panel__title">
          Recent evaluation runs
        </h2>
      </div>
      {summary.recent_evaluation_runs.length === 0 ? (
        <div className="panel__body">
          <EmptyState title="No evaluation runs yet">
            Evaluation runs appear here once you run a test set against N-ATLAS.
          </EmptyState>
        </div>
      ) : (
        <div className="panel__body panel__body--flush table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Evaluation</th>
                <th>Model</th>
                <th>Status</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {summary.recent_evaluation_runs.map((run) => (
                <tr key={run.id}>
                  <td>{run.evaluation_name ?? "Untitled"}</td>
                  <td className="mono">{run.model ?? "Unknown"}</td>
                  <td>{run.status}</td>
                  <td>{formatRelative(run.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function DashboardPage() {
  useDocumentTitle("Dashboard");
  const { data, error, loading, reload } = useAsyncData(() => api.dashboard.summary(30), []);

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Activity across your projects. Every figure is computed from stored request data."
      />
      {loading && !data ? <Spinner label="Loading the dashboard" /> : null}
      <ErrorNotice error={error} onRetry={reload} />
      {data ? (
        <>
          <ProviderPanel provider={data.provider} />
          <Stats summary={data} />
          {data.totals.projects === 0 ? <Onboarding /> : null}
          <RecentRequests summary={data} />
          <div className="grid-2">
            <KeysPanel summary={data} />
            <EvaluationPanel summary={data} />
          </div>
        </>
      ) : null}
    </>
  );
}
