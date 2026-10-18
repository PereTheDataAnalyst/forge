import "./evaluation.css";
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { EmptyState } from "../../components/EmptyState";
import { ErrorNotice } from "../../components/ErrorNotice";
import { PageHeader } from "../../components/PageHeader";
import { Spinner } from "../../components/Spinner";
import { StatusPill } from "../../components/StatusPill";
import { api } from "../../lib/api";
import { formatRelative } from "../../lib/format";
import type { Project } from "../../lib/types";
import { useAsyncData } from "../../lib/useAsyncData";
import { useDocumentTitle } from "../../lib/useDocumentTitle";
import {
  EXAMPLE_DATASET_TEXT,
  RUN_STATE,
  describeParams,
  formatRate,
  isValidPromptVersion,
  parseDatasetText,
  shortRunId,
} from "./logic";

function Field({
  id,
  label,
  hint,
  error,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>
        {label}
      </label>
      {children}
      {hint ? <p className="field__hint">{hint}</p> : null}
      {error ? (
        <p className="field__error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function preview(text: string, length = 90): string {
  const single = text.replace(/\s+/g, " ").trim();
  return single.length > length ? `${single.slice(0, length)}...` : single;
}

/** The cases of one dataset, loaded when the row is opened. */
function DatasetCases({ projectId, evaluationId }: { projectId: string; evaluationId: string }) {
  const detail = useAsyncData(
    () => api.evaluations.get(projectId, evaluationId),
    [projectId, evaluationId],
  );
  if (detail.loading && !detail.data) return <Spinner label="Loading the cases" />;
  if (detail.error) return <ErrorNotice error={detail.error} onRetry={detail.reload} />;
  if (!detail.data) return null;
  return (
    <div className="table-wrap">
      <table className="table ev-cases">
        <thead>
          <tr>
            <th>#</th>
            <th>Case</th>
            <th>Language</th>
            <th>Checks</th>
            <th>Prompt</th>
          </tr>
        </thead>
        <tbody>
          {detail.data.cases.map((item) => (
            <tr key={item.id}>
              <td>{item.position + 1}</td>
              <td>
                <strong>{item.name ?? item.key}</strong>
                <div className="muted">{item.category}</div>
              </td>
              <td>{item.language ?? "Not set"}</td>
              <td className="mono">{item.checks.map((check) => check.type).join(", ")}</td>
              <td>{preview(item.prompt)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type AddMode = "starter" | "json";

function AddDatasetPanel({
  projectId,
  onAdded,
}: {
  projectId: string;
  onAdded: (message: string) => void;
}) {
  const baseId = useId();
  const starters = useAsyncData(() => api.evaluations.starters(), []);
  const checks = useAsyncData(() => api.evaluations.checks(), []);
  const [mode, setMode] = useState<AddMode>("starter");
  const [starterId, setStarterId] = useState("");
  const [text, setText] = useState("");
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const firstStarter = starters.data && starters.data.length > 0 ? starters.data[0].id : "";
  const chosenStarter = starterId || firstStarter;

  async function add() {
    setError(null);
    let body: { starter_id: string } | { dataset: unknown };
    if (mode === "starter") {
      body = { starter_id: chosenStarter };
    } else {
      const parsed = parseDatasetText(text);
      if ("error" in parsed) {
        setError(new Error(parsed.error));
        return;
      }
      body = { dataset: parsed.value };
    }
    setAdding(true);
    try {
      const created = await api.evaluations.create(projectId, body);
      onAdded(`Added "${created.name}" with ${created.case_count} cases.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error("The dataset could not be added."));
    } finally {
      setAdding(false);
    }
  }

  return (
    <section className="panel" aria-labelledby={`${baseId}-title`}>
      <div className="panel__header">
        <h2 id={`${baseId}-title`} className="panel__title">
          Add a dataset
        </h2>
        <div className="ev-seg" role="group" aria-label="Where the dataset comes from">
          <button type="button" aria-pressed={mode === "starter"} onClick={() => setMode("starter")}>
            Starter
          </button>
          <button type="button" aria-pressed={mode === "json"} onClick={() => setMode("json")}>
            Paste JSON
          </button>
        </div>
      </div>
      <div className="panel__body ev-form">
        {mode === "starter" ? (
          <>
            <ErrorNotice error={starters.error} onRetry={starters.reload} />
            {starters.loading && !starters.data ? <Spinner label="Loading the starters" /> : null}
            <div className="checks" role="radiogroup" aria-label="Starter datasets">
              {(starters.data ?? []).map((starter) => (
                <label key={starter.id} className="check ev-starter">
                  <input
                    type="radio"
                    name={`${baseId}-starter`}
                    checked={chosenStarter === starter.id}
                    onChange={() => setStarterId(starter.id)}
                  />
                  <span>
                    <strong>{starter.name}</strong>
                    <span className="muted">
                      {" "}
                      {starter.case_count} cases, {starter.languages.join(", ")}
                    </span>
                    <span className="ev-starter__text">{starter.description}</span>
                  </span>
                </label>
              ))}
            </div>
          </>
        ) : (
          <>
            <Field
              id={`${baseId}-json`}
              label="Dataset JSON"
              hint="A name and a list of cases. Each case has a prompt, a category and checks. The server validates it and names every problem."
            >
              <textarea
                id={`${baseId}-json`}
                className="textarea ev-json"
                spellCheck={false}
                value={text}
                onChange={(event) => setText(event.target.value)}
              />
            </Field>
            <div>
              <button
                type="button"
                className="btn btn--secondary btn--sm"
                onClick={() => setText(EXAMPLE_DATASET_TEXT)}
              >
                Insert an example
              </button>
            </div>
            <details className="ev-help">
              <summary>Checks you can use</summary>
              {checks.data ? (
                <dl className="ev-checks">
                  {checks.data.map((check) => (
                    <div key={check.type}>
                      <dt className="mono">{check.type}</dt>
                      <dd>
                        {check.description}
                        <span className="muted"> Parameters: {describeParams(check.params)}.</span>
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="muted">
                  {checks.error ? "The list of checks could not be loaded." : "Loading the checks."}
                </p>
              )}
            </details>
          </>
        )}
        <ErrorNotice error={error} />
        <div>
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => void add()}
            disabled={adding || (mode === "starter" && !chosenStarter)}
          >
            {adding ? "Adding" : "Add dataset"}
          </button>
        </div>
      </div>
    </section>
  );
}

function ProjectEvaluations({
  projectId,
  projects,
  onProjectChange,
}: {
  projectId: string;
  projects: Project[];
  onProjectChange: (id: string) => void;
}) {
  const baseId = useId();
  const navigate = useNavigate();
  const [datasetsTick, setDatasetsTick] = useState(0);
  const [runsTick, setRunsTick] = useState(0);
  const datasets = useAsyncData(() => api.evaluations.list(projectId), [projectId, datasetsTick]);
  const runs = useAsyncData(() => api.evaluations.runs(projectId), [projectId, runsTick]);
  const models = useAsyncData(() => api.playground.models(projectId), [projectId]);

  const modelList = useMemo(() => models.data ?? [], [models.data]);
  const [modelId, setModelId] = useState("");
  const firstModelId = modelList.length > 0 ? modelList[0].id : "";
  useEffect(() => {
    if (firstModelId && !modelList.some((model) => model.id === modelId)) setModelId(firstModelId);
  }, [firstModelId, modelId, modelList]);
  const selectedModel = modelList.find((model) => model.id === modelId) ?? null;
  const modelAway = !selectedModel || selectedModel.status !== "available";

  const [promptVersion, setPromptVersion] = useState("");
  const promptVersionValid = isValidPromptVersion(promptVersion);

  const [addOpen, setAddOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [starting, setStarting] = useState<string | null>(null);
  const [actionError, setActionError] = useState<Error | null>(null);

  // Refresh the runs list while a run is in progress, and once more shortly after it ends:
  // a finished run is saved a moment after it ends, and the list reads the saved copy.
  const anyRunning = (runs.data ?? []).some((run) => run.state === "running");
  const wasRunning = useRef(false);
  useEffect(() => {
    if (anyRunning) {
      wasRunning.current = true;
      const timer = window.setInterval(() => setRunsTick((value) => value + 1), 3000);
      return () => window.clearInterval(timer);
    }
    if (!wasRunning.current) return undefined;
    wasRunning.current = false;
    const timers = [1500, 5000].map((delay) =>
      window.setTimeout(() => setRunsTick((value) => value + 1), delay),
    );
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [anyRunning]);

  async function startRun(evaluationId: string) {
    setActionError(null);
    setStarting(evaluationId);
    try {
      const handle = await api.evaluations.startRun(projectId, {
        evaluation_id: evaluationId,
        model: modelId || undefined,
        prompt_version: promptVersion.trim() || undefined,
      });
      navigate(`/app/evaluations/${projectId}/runs/${handle.run_id}`);
    } catch (caught) {
      setActionError(caught instanceof Error ? caught : new Error("The run could not start."));
      setRunsTick((value) => value + 1);
    } finally {
      setStarting(null);
    }
  }

  async function archive(evaluationId: string) {
    setActionError(null);
    try {
      await api.evaluations.archive(projectId, evaluationId);
      setConfirming(null);
      setExpanded(null);
      setNotice("Dataset archived. Its past runs are still available.");
      setDatasetsTick((value) => value + 1);
    } catch (caught) {
      setActionError(caught instanceof Error ? caught : new Error("The dataset could not be archived."));
    }
  }

  return (
    <>
      <div className="ev-toolbar">
        <Field id={`${baseId}-project`} label="Project">
          <select
            id={`${baseId}-project`}
            className="select"
            value={projectId}
            onChange={(event) => onProjectChange(event.target.value)}
          >
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
        </Field>
        <Field
          id={`${baseId}-model`}
          label="Model"
          hint={selectedModel ? undefined : "No text model is listed."}
        >
          <select
            id={`${baseId}-model`}
            className="select"
            value={modelId}
            onChange={(event) => setModelId(event.target.value)}
            disabled={modelList.length === 0}
          >
            {modelList.length === 0 ? <option value="">No model listed</option> : null}
            {modelList.map((model) => (
              <option key={model.id} value={model.id}>
                {model.id}
              </option>
            ))}
          </select>
        </Field>
        <Field
          id={`${baseId}-version`}
          label="Prompt version"
          hint="Optional label such as v2, to tell runs apart."
          error={promptVersionValid ? undefined : "Use letters, digits and . _ : / @ + # - only."}
        >
          <input
            id={`${baseId}-version`}
            className="input"
            maxLength={64}
            value={promptVersion}
            aria-invalid={promptVersionValid ? undefined : "true"}
            onChange={(event) => setPromptVersion(event.target.value)}
          />
        </Field>
      </div>

      {!models.loading && modelAway ? (
        <p className="notice notice--warning" role="status">
          N-ATLAS is not connected right now, so runs cannot start. The dashboard shows the same
          status.
        </p>
      ) : null}
      {notice ? (
        <p className="notice notice--info" role="status">
          {notice}
        </p>
      ) : null}
      <ErrorNotice error={actionError} />

      {addOpen ? (
        <AddDatasetPanel
          projectId={projectId}
          onAdded={(message) => {
            setNotice(message);
            setAddOpen(false);
            setDatasetsTick((value) => value + 1);
          }}
        />
      ) : null}

      <section className="panel" aria-labelledby={`${baseId}-datasets`}>
        <div className="panel__header">
          <h2 id={`${baseId}-datasets`} className="panel__title">
            Datasets
          </h2>
          <button
            type="button"
            className="btn btn--primary btn--sm"
            aria-expanded={addOpen}
            onClick={() => setAddOpen(!addOpen)}
          >
            {addOpen ? "Close" : "Add dataset"}
          </button>
        </div>
        <ErrorNotice error={datasets.error} onRetry={() => setDatasetsTick((value) => value + 1)} />
        {datasets.loading && !datasets.data ? (
          <div className="panel__body">
            <Spinner label="Loading the datasets" />
          </div>
        ) : null}
        {datasets.data && datasets.data.length === 0 ? (
          <div className="panel__body">
            <EmptyState title="No datasets yet">
              A dataset is a list of prompts with checks that the answers must meet. Add a starter
              to see how a run works, or paste your own.
            </EmptyState>
          </div>
        ) : null}
        {datasets.data && datasets.data.length > 0 ? (
          <div className="panel__body panel__body--flush table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Dataset</th>
                  <th>Cases</th>
                  <th>Added</th>
                  <th>
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {datasets.data.map((dataset) => (
                  <tr key={dataset.id} className="ev-row">
                    <td>
                      <strong>{dataset.name}</strong>
                      {dataset.description ? (
                        <div className="muted">{preview(dataset.description, 140)}</div>
                      ) : null}
                      {expanded === dataset.id ? (
                        <div className="ev-expanded">
                          <DatasetCases projectId={projectId} evaluationId={dataset.id} />
                        </div>
                      ) : null}
                    </td>
                    <td>{dataset.case_count}</td>
                    <td>{formatRelative(dataset.created_at)}</td>
                    <td>
                      <div className="ev-actions">
                        <button
                          type="button"
                          className="btn btn--primary btn--sm"
                          onClick={() => void startRun(dataset.id)}
                          disabled={starting !== null || modelAway || !promptVersionValid}
                        >
                          {starting === dataset.id ? "Starting" : "Run"}
                        </button>
                        <button
                          type="button"
                          className="btn btn--ghost btn--sm"
                          aria-expanded={expanded === dataset.id}
                          onClick={() => setExpanded(expanded === dataset.id ? null : dataset.id)}
                        >
                          {expanded === dataset.id ? "Hide cases" : "View cases"}
                        </button>
                        {confirming === dataset.id ? (
                          <>
                            <button
                              type="button"
                              className="btn btn--danger btn--sm"
                              onClick={() => void archive(dataset.id)}
                            >
                              Confirm archive
                            </button>
                            <button
                              type="button"
                              className="btn btn--ghost btn--sm"
                              onClick={() => setConfirming(null)}
                            >
                              Keep
                            </button>
                          </>
                        ) : (
                          <button
                            type="button"
                            className="btn btn--ghost btn--sm"
                            onClick={() => setConfirming(dataset.id)}
                          >
                            Archive
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <section className="panel" aria-labelledby={`${baseId}-runs`}>
        <div className="panel__header">
          <h2 id={`${baseId}-runs`} className="panel__title">
            Runs
          </h2>
        </div>
        <ErrorNotice error={runs.error} onRetry={() => setRunsTick((value) => value + 1)} />
        {runs.loading && !runs.data ? (
          <div className="panel__body">
            <Spinner label="Loading the runs" />
          </div>
        ) : null}
        {runs.data && runs.data.length === 0 ? (
          <div className="panel__body">
            <EmptyState title="No runs yet">
              Press Run on a dataset. Every case goes through the gateway to N-ATLAS, and the
              results appear here.
            </EmptyState>
          </div>
        ) : null}
        {runs.data && runs.data.length > 0 ? (
          <div className="panel__body panel__body--flush table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Dataset</th>
                  <th>Status</th>
                  <th>Pass rate</th>
                  <th>Cases</th>
                  <th>Model</th>
                  <th>Started</th>
                  <th>
                    <span className="visually-hidden">Open</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {runs.data.map((run) => {
                  const state = RUN_STATE[run.state];
                  return (
                    <tr key={run.run_id}>
                      <td>
                        <strong>{run.dataset_name ?? "Untitled"}</strong>
                        <div className="muted mono">
                          {shortRunId(run.run_id)}
                          {run.prompt_version ? `  ${run.prompt_version}` : ""}
                        </div>
                      </td>
                      <td>
                        <StatusPill tone={state.tone}>{state.label}</StatusPill>
                      </td>
                      <td>{run.state === "running" ? "In progress" : formatRate(run.pass_rate)}</td>
                      <td>
                        {run.state === "running"
                          ? `${run.cases_done ?? 0} of ${run.cases_total ?? 0}`
                          : (run.cases_total ?? 0)}
                      </td>
                      <td className="mono">{run.model ?? "Unknown"}</td>
                      <td>{formatRelative(run.started_at)}</td>
                      <td>
                        <Link
                          to={`/app/evaluations/${projectId}/runs/${run.run_id}`}
                          className="btn btn--secondary btn--sm"
                        >
                          Open
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </>
  );
}

export function EvaluationsPage() {
  useDocumentTitle("Evaluations");
  const projects = useAsyncData(() => api.projects.list(), []);
  const [projectId, setProjectId] = useState("");

  const firstProjectId = projects.data && projects.data.length > 0 ? projects.data[0].id : "";
  useEffect(() => {
    if (!projectId && firstProjectId) setProjectId(firstProjectId);
  }, [projectId, firstProjectId]);

  return (
    <>
      <PageHeader
        title="Evaluations"
        description="Test N-ATLAS against checks you define. A run sends every case through the gateway and reports which checks held. Scores come from your rules: there is no accuracy score and no model-based judge."
      />
      {projects.loading && !projects.data ? <Spinner label="Loading your projects" /> : null}
      <ErrorNotice error={projects.error} onRetry={projects.reload} />
      {projects.data && projects.data.length === 0 ? (
        <EmptyState
          title="Create a project first"
          action={
            <Link to="/app/projects" className="btn btn--primary">
              Go to projects
            </Link>
          }
        >
          Evaluation runs are counted against a project, so you can see them in its request logs
          and usage.
        </EmptyState>
      ) : null}
      {projects.data && projects.data.length > 0 && projectId ? (
        <ProjectEvaluations
          key={projectId}
          projectId={projectId}
          projects={projects.data}
          onProjectChange={setProjectId}
        />
      ) : null}
    </>
  );
}
