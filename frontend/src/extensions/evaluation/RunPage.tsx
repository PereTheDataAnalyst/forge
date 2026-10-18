import "./evaluation.css";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ErrorNotice } from "../../components/ErrorNotice";
import { PageHeader } from "../../components/PageHeader";
import { Spinner } from "../../components/Spinner";
import { StatusPill } from "../../components/StatusPill";
import { languages } from "../../config";
import { ApiError, api } from "../../lib/api";
import { formatDateTime, formatLatency, formatNumber } from "../../lib/format";
import type { CaseResult, GroupSummary, RunDocument, RunStatus } from "../../lib/types";
import { useDocumentTitle } from "../../lib/useDocumentTitle";
import {
  RUN_STATE,
  STATUS_HINT,
  STATUS_LABEL,
  STATUS_ORDER,
  STATUS_TONE,
  attentionCount,
  checksHeld,
  describeErrorCode,
  filterResults,
  formatRate,
  progressPercent,
  shortRunId,
  type ResultFilter,
} from "./logic";

const POLL_MS = 1500;
const RETRY_MS = 3000;
const MAX_TRANSIENT_FAILURES = 3;

function languageName(code: string): string {
  return languages.find((language) => language.code === code)?.name ?? code;
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="stat">
      <div className="stat__label">{label}</div>
      <div className="stat__value">{value}</div>
      {note ? <div className="stat__note">{note}</div> : null}
    </div>
  );
}

function Breakdown({
  title,
  column,
  groups,
  label,
}: {
  title: string;
  column: string;
  groups: Record<string, GroupSummary>;
  label: (key: string) => string;
}) {
  const entries = Object.entries(groups);
  if (entries.length === 0) return null;
  return (
    <section className="panel">
      <div className="panel__header">
        <h2 className="panel__title">{title}</h2>
      </div>
      <div className="panel__body panel__body--flush table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>{column}</th>
              <th>Cases</th>
              <th>Passed</th>
              <th>Failed</th>
              <th>Needs review</th>
              <th>Errors</th>
              <th>Pass rate</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([key, group]) => (
              <tr key={key}>
                <td>{label(key)}</td>
                <td>{formatNumber(group.total)}</td>
                <td>{formatNumber(group.counts.pass)}</td>
                <td>{formatNumber(group.counts.fail)}</td>
                <td>{formatNumber(group.counts.review)}</td>
                <td>{formatNumber(group.counts.error)}</td>
                <td>{formatRate(group.pass_rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function CaseDetails({ result }: { result: CaseResult }) {
  const { held, total } = checksHeld(result.checks);
  const measured = result.attempts.filter((attempt) => attempt.ok && attempt.latency_ms !== null);
  const slowest =
    measured.length > 0 ? Math.max(...measured.map((attempt) => attempt.latency_ms ?? 0)) : null;
  return (
    <details className="ev-case">
      <summary>
        <span title={STATUS_HINT[result.status]}>
          <StatusPill tone={STATUS_TONE[result.status]}>{STATUS_LABEL[result.status]}</StatusPill>
        </span>
        <span className="ev-case__name">{result.name}</span>
        <span className="ev-case__meta">
          {result.category}
          {result.language ? `, ${languageName(result.language)}` : ""}
          {total > 0 ? `, ${held} of ${total} checks held` : ""}
          {slowest !== null ? `, ${formatLatency(slowest)}` : ""}
        </span>
      </summary>
      <div className="ev-case__body">
        <p className="muted">{STATUS_HINT[result.status]}</p>

        {result.error_code ? (
          <p className="notice notice--danger" role="status">
            <span className="notice__message">{describeErrorCode(result.error_code)}</span>
            {result.error_message ? <span className="notice__meta">{result.error_message}</span> : null}
          </p>
        ) : null}

        {result.attempts.map((attempt) => (
          <div key={attempt.index} className="ev-attempt">
            <p className="ev-attempt__head">
              <strong>
                {result.attempts.length > 1
                  ? `Attempt ${attempt.index + 1}`
                  : attempt.ok
                    ? "Model output"
                    : "Model call"}
              </strong>
              <span className="muted">
                {attempt.ok ? formatLatency(attempt.latency_ms) : "Failed"}
                {attempt.usage?.total_tokens ? `, ${formatNumber(attempt.usage.total_tokens)} tokens` : ""}
                {attempt.finish_reason ? `, ${attempt.finish_reason}` : ""}
              </span>
              <span className="mono muted">{attempt.request_id}</span>
            </p>
            {attempt.ok ? (
              <pre className="ev-output">{attempt.output ?? ""}</pre>
            ) : (
              <p className="muted">{describeErrorCode(attempt.error_code)}</p>
            )}
            {attempt.output_truncated ? (
              <p className="muted">The stored output was cut at the size limit.</p>
            ) : null}
          </div>
        ))}

        {result.checks.length > 0 ? (
          <ul className="ev-checklist">
            {result.checks.map((check, index) => (
              <li key={`${check.type}-${index}`}>
                <StatusPill
                  tone={check.passed === true ? "success" : check.passed === false ? "danger" : "warning"}
                >
                  {check.passed === true ? "Held" : check.passed === false ? "Did not hold" : "Undecided"}
                </StatusPill>
                <span className="mono">{check.type}</span>
                <span>{check.detail}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </details>
  );
}

function Results({ run }: { run: RunDocument }) {
  const [filter, setFilter] = useState<ResultFilter>("all");
  const attention = attentionCount(run.summary.counts);
  const shown = filterResults(run.results, filter);
  return (
    <section className="panel" aria-labelledby="ev-results-title">
      <div className="panel__header">
        <h2 id="ev-results-title" className="panel__title">
          Cases
        </h2>
        <div className="ev-seg" role="group" aria-label="Which cases to show">
          <button type="button" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>
            All ({run.results.length})
          </button>
          <button
            type="button"
            aria-pressed={filter === "attention"}
            onClick={() => setFilter("attention")}
          >
            Needs attention ({attention})
          </button>
        </div>
      </div>
      <div className="panel__body ev-cases-list">
        {shown.length === 0 ? (
          <p className="muted">
            {filter === "attention"
              ? "Every case passed. Nothing needs attention."
              : "This run has no cases."}
          </p>
        ) : (
          shown.map((result) => <CaseDetails key={result.case_id} result={result} />)
        )}
      </div>
    </section>
  );
}

export function RunPage() {
  useDocumentTitle("Evaluation run");
  const { projectId = "", runId = "" } = useParams();
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [retries, setRetries] = useState(0);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<Error | null>(null);

  useEffect(() => {
    if (!projectId || !runId) return undefined;
    let stopped = false;
    let timer: number | undefined;
    let failures = 0;
    const controller = new AbortController();

    async function poll() {
      try {
        const next = await api.evaluations.run(projectId, runId, controller.signal);
        if (stopped) return;
        failures = 0;
        setStatus(next);
        setError(null);
        // Keep looking while the run is going, and while a run that just ended is being saved.
        const saving = next.handle !== null && next.handle.persisted === null;
        if (next.state === "running" || (saving && next.state !== "failed")) {
          timer = window.setTimeout(() => void poll(), POLL_MS);
        }
      } catch (caught) {
        if (stopped) return;
        // A client error (for example 404) will not fix itself. Network blips and server errors
        // get a few quiet retries before the page complains.
        const permanent = caught instanceof ApiError && caught.status >= 400 && caught.status < 500;
        failures += 1;
        if (!permanent && failures <= MAX_TRANSIENT_FAILURES) {
          timer = window.setTimeout(() => void poll(), RETRY_MS);
          return;
        }
        setError(caught instanceof Error ? caught : new Error("Could not load the run."));
      }
    }

    void poll();
    return () => {
      stopped = true;
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [projectId, runId, retries]);

  async function cancel() {
    setCancelling(true);
    setCancelError(null);
    try {
      await api.evaluations.cancel(projectId, runId);
    } catch (caught) {
      setCancelError(caught instanceof Error ? caught : new Error("The run could not be cancelled."));
      setCancelling(false);
    }
  }

  const state = status?.state ?? null;
  const handle = status?.handle ?? null;
  const run = status?.run ?? null;
  const running = state === "running";
  const total = handle?.cases_total ?? run?.results.length ?? 0;
  const done = handle?.cases_done ?? total;
  const name = run?.dataset_name ?? handle?.dataset_name ?? "Evaluation run";
  const stateLabel = state ? RUN_STATE[state] : null;

  const details = [
    `Run ${shortRunId(runId)}`,
    run?.model ? run.model : null,
    run?.prompt_version ? `prompt ${run.prompt_version}` : null,
    run ? formatDateTime(run.started_at) : null,
  ].filter((part): part is string => part !== null);

  return (
    <>
      <p className="ev-back">
        <Link to="/app/evaluations">All evaluations</Link>
      </p>
      <PageHeader title={name} description={details.join(", ")} />
      {!status && !error ? <Spinner label="Loading the run" /> : null}
      <ErrorNotice error={error} onRetry={() => setRetries((value) => value + 1)} />

      {status && stateLabel ? (
        <div className="ev-state">
          <StatusPill tone={stateLabel.tone}>{stateLabel.label}</StatusPill>
          {handle?.persisted === null && !running && state !== "failed" ? (
            <span className="muted">Saving the run</span>
          ) : null}
        </div>
      ) : null}

      {running ? (
        <section className="panel" aria-live="polite">
          <div className="panel__body ev-progress">
            <div className="ev-progress__head">
              <strong>
                {done} of {total} cases finished
              </strong>
              <button
                type="button"
                className="btn btn--secondary btn--sm"
                onClick={() => void cancel()}
                disabled={cancelling}
              >
                {cancelling ? "Cancelling" : "Cancel run"}
              </button>
            </div>
            <progress
              className="ev-bar"
              max={100}
              value={progressPercent(done, total)}
              aria-label="Run progress"
            />
            <p className="muted">
              Cases run through the gateway one or two at a time. Cancelling lets the cases already
              running finish and skips the rest.
            </p>
            <ErrorNotice error={cancelError} />
          </div>
        </section>
      ) : null}

      {state === "failed" ? (
        <p className="notice notice--danger" role="alert">
          <span className="notice__message">
            {handle?.error_message ?? "The run could not be completed."}
          </span>
          <span className="notice__meta">Nothing was stored for this run. Start it again.</span>
        </p>
      ) : null}

      {state === "cancelled" && run ? (
        <p className="notice notice--info" role="status">
          This run was cancelled. {formatNumber(run.summary.counts.skipped)} cases did not start and
          are shown as skipped. The results below cover the cases that finished.
        </p>
      ) : null}

      {handle?.persisted === false && run ? (
        <p className="notice notice--warning" role="status">
          This run finished but could not be saved to the database. You can read and export it
          now, but it will be lost when the service restarts.
        </p>
      ) : null}

      {run ? (
        <>
          <div className="stats">
            <Stat
              label="Pass rate"
              value={formatRate(run.summary.pass_rate)}
              note="Share of completed cases whose checks all held. Not model accuracy."
            />
            {STATUS_ORDER.filter((key) => key !== "skipped" || run.summary.counts.skipped > 0).map(
              (key) => (
                <Stat
                  key={key}
                  label={STATUS_LABEL[key]}
                  value={formatNumber(run.summary.counts[key])}
                  note={key === "error" ? "Model call failed" : undefined}
                />
              ),
            )}
            <Stat label="Median time" value={formatLatency(run.summary.latency_ms?.p50)} />
            <Stat label="95th percentile" value={formatLatency(run.summary.latency_ms?.p95)} />
          </div>

          <div className="ev-export">
            <a
              className="btn btn--secondary btn--sm"
              href={api.evaluations.exportUrl(projectId, runId, "csv")}
              download
            >
              Download CSV
            </a>
            <a
              className="btn btn--secondary btn--sm"
              href={api.evaluations.exportUrl(projectId, runId, "json")}
              download
            >
              Download JSON
            </a>
            <span className="muted">Exports contain the model outputs.</span>
          </div>

          <Breakdown
            title="By language"
            column="Language"
            groups={run.summary.by_language}
            label={languageName}
          />
          <Breakdown
            title="By category"
            column="Category"
            groups={run.summary.by_category}
            label={(key) => key}
          />
          <Results run={run} />

          <details className="panel ev-method">
            <summary>How these numbers are made</summary>
            <ul>
              {run.methodology.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </details>
        </>
      ) : null}
    </>
  );
}
