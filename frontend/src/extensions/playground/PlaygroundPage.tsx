import "./playground.css";
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { CodeBlock } from "../../components/CodeBlock";
import { CopyButton } from "../../components/CopyButton";
import { EmptyState } from "../../components/EmptyState";
import { ErrorNotice } from "../../components/ErrorNotice";
import { PageHeader } from "../../components/PageHeader";
import { Spinner } from "../../components/Spinner";
import { StatusPill, type Tone } from "../../components/StatusPill";
import { DEFAULT_MODEL_ID, languages } from "../../config";
import { ApiError, api } from "../../lib/api";
import { formatLatency, formatNumber } from "../../lib/format";
import { buildSnippet, type ChatSnippetInput, type SnippetLanguage } from "../../lib/snippets";
import type {
  ChatRequestBody,
  ChatResponse,
  LanguageCode,
  ModelInfo,
  ModelStatus,
} from "../../lib/types";
import { useAsyncData } from "../../lib/useAsyncData";
import { useDocumentTitle } from "../../lib/useDocumentTitle";
import {
  DEFAULT_PARAMS,
  PARAM_LABELS,
  PARAM_LIMITS,
  buildMessages,
  buildRequestBody,
  describeFinish,
  exportConversation,
  nextTurnId,
  parseParams,
  toFields,
  type ParamErrors,
  type ParamField,
  type ParamFields,
  type Turn,
} from "./logic";

const MODEL_STATUS: Record<ModelStatus, { tone: Tone; label: string }> = {
  available: { tone: "success", label: "Connected" },
  loading: { tone: "warning", label: "Model loading" },
  unavailable: { tone: "danger", label: "Unreachable" },
  not_configured: { tone: "neutral", label: "Not connected" },
};

const PARAM_ORDER: ParamField[] = ["temperature", "topP", "maxTokens", "repetitionPenalty"];

const PARAM_HINTS: Record<ParamField, string> = {
  temperature: "0 to 2. Zero selects greedy decoding.",
  topP: "Above 0 and at most 1.",
  maxTokens: "1 to 2048. The deployment sets the cap.",
  repetitionPenalty: "1 to 2. The model card example uses 1.12.",
};

const SNIPPET_KINDS: { id: SnippetLanguage; label: string }[] = [
  { id: "curl", label: "cURL" },
  { id: "powershell", label: "PowerShell" },
  { id: "javascript", label: "JavaScript" },
  { id: "python", label: "Python" },
];

const PLACEHOLDER_PROMPT = "Say hello in one short sentence.";

type DetailTab = "request" | "response" | "code";
type MobileView = "chat" | "settings" | "details";

interface Exchange {
  request: ChatRequestBody;
  response: ChatResponse | null;
  error: Error | null;
  cancelled: boolean;
  roundTripMs: number | null;
}

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

function errorBody(error: Error): string {
  const api = error instanceof ApiError ? error : null;
  return JSON.stringify(
    {
      error: {
        code: api?.code ?? "error",
        message: error.message,
        request_id: api?.requestId ?? null,
      },
    },
    null,
    2,
  );
}

export function PlaygroundPage() {
  useDocumentTitle("Playground");
  const baseId = useId();
  const fieldId = (name: string) => `${baseId}-${name}`;

  const projects = useAsyncData(() => api.projects.list(), []);
  const [projectId, setProjectId] = useState("");
  const models = useAsyncData(
    () => (projectId ? api.playground.models(projectId) : Promise.resolve([] as ModelInfo[])),
    [projectId],
  );
  const [modelId, setModelId] = useState("");
  const [system, setSystem] = useState("");
  const [language, setLanguage] = useState<LanguageCode | "">("");
  const [fields, setFields] = useState<ParamFields>(toFields(DEFAULT_PARAMS));
  const [paramErrors, setParamErrors] = useState<ParamErrors>({});

  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [last, setLast] = useState<Exchange | null>(null);

  // Open by default only where there is room for three columns.
  const [detailsOpen, setDetailsOpen] = useState(() => window.innerWidth >= 1500);
  const [detailTab, setDetailTab] = useState<DetailTab>("request");
  const [snippetKind, setSnippetKind] = useState<SnippetLanguage>("curl");
  const [mobileView, setMobileView] = useState<MobileView>("chat");

  const abortRef = useRef<AbortController | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const firstProjectId = projects.data && projects.data.length > 0 ? projects.data[0].id : "";
  useEffect(() => {
    if (!projectId && firstProjectId) setProjectId(firstProjectId);
  }, [projectId, firstProjectId]);

  const modelList = useMemo(() => models.data ?? [], [models.data]);
  const firstModelId = modelList.length > 0 ? modelList[0].id : "";
  useEffect(() => {
    if (firstModelId && !modelList.some((model) => model.id === modelId)) {
      setModelId(firstModelId);
    }
  }, [firstModelId, modelId, modelList]);
  const selectedModel = modelList.find((model) => model.id === modelId) ?? null;

  useEffect(() => {
    const element = listRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [turns, sending]);

  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  async function send(text: string, baseTurns: Turn[]) {
    const prompt = text.trim();
    if (!prompt || !projectId || sending) return;
    const parsed = parseParams(fields);
    if (!parsed.params) {
      setParamErrors(parsed.errors);
      setMobileView("settings");
      return;
    }
    setParamErrors({});

    const history: Turn[] = [...baseTurns, { id: nextTurnId(), role: "user", content: prompt }];
    const body = buildRequestBody({
      model: modelId,
      system,
      turns: history,
      params: parsed.params,
      language,
    });
    const controller = new AbortController();
    abortRef.current = controller;
    setTurns(history);
    setDraft("");
    setError(null);
    setNotice(null);
    setSending(true);
    const started = performance.now();

    try {
      const response = await api.playground.chat(projectId, body, controller.signal);
      const roundTripMs = Math.round(performance.now() - started);
      setTurns([
        ...history,
        {
          id: nextTurnId(),
          role: "assistant",
          content: response.message.content,
          meta: {
            requestId: response.request_id,
            model: response.model,
            finishReason: response.finish_reason,
            promptTokens: response.usage.prompt_tokens,
            completionTokens: response.usage.completion_tokens,
            totalTokens: response.usage.total_tokens,
            gatewayLatencyMs: response.latency_ms,
            roundTripMs,
          },
        },
      ]);
      setLast({ request: body, response, error: null, cancelled: false, roundTripMs });
    } catch (caught) {
      const roundTripMs = Math.round(performance.now() - started);
      const cancelled = caught instanceof DOMException && caught.name === "AbortError";
      // Take the unanswered message back so the history stays valid and the text is not lost.
      setTurns(baseTurns);
      setDraft((current) => (current.trim() ? current : prompt));
      if (cancelled) {
        setNotice(
          "Stopped. Your browser stopped waiting, but the model host may still finish this reply.",
        );
        setLast({ request: body, response: null, error: null, cancelled: true, roundTripMs });
      } else {
        const failure = caught instanceof Error ? caught : new Error("The request failed.");
        setError(failure);
        setLast({ request: body, response: null, error: failure, cancelled: false, roundTripMs });
      }
    } finally {
      abortRef.current = null;
      setSending(false);
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  function clearConversation() {
    if (sending) return;
    setTurns([]);
    setLast(null);
    setError(null);
    setNotice(null);
  }

  const canRegenerate =
    !sending && turns.length > 0 && turns[turns.length - 1].role === "assistant";

  function regenerate() {
    if (!canRegenerate) return;
    let index = -1;
    turns.forEach((turn, position) => {
      if (turn.role === "user") index = position;
    });
    if (index >= 0) void send(turns[index].content, turns.slice(0, index));
  }

  function changeParam(field: ParamField, value: string) {
    setFields({ ...fields, [field]: value });
    if (paramErrors[field]) setParamErrors({ ...paramErrors, [field]: undefined });
  }

  function snippetInput(): ChatSnippetInput {
    const request = last?.request;
    const fallback = parseParams(fields).params ?? DEFAULT_PARAMS;
    const messages = request
      ? request.messages
      : buildMessages(system, [
          { id: "draft", role: "user", content: draft.trim() || PLACEHOLDER_PROMPT },
        ]);
    return {
      baseUrl: window.location.origin,
      model: request?.model ?? (modelId || DEFAULT_MODEL_ID),
      prompt: "",
      language: request?.language ?? language,
      temperature: request?.temperature ?? fallback.temperature,
      maxTokens: request?.max_tokens ?? fallback.maxTokens,
      topP: request?.top_p ?? fallback.topP,
      repetitionPenalty: request?.repetition_penalty ?? fallback.repetitionPenalty,
      messages,
    };
  }

  const status = selectedModel ? MODEL_STATUS[selectedModel.status] : null;
  const hasProjects = Boolean(projects.data && projects.data.length > 0);

  return (
    <>
      <PageHeader
        title="Playground"
        description="Try prompts against N-ATLAS from your browser. Requests use your signed-in session, go through the same gateway as API keys and appear in your request logs. Prompts and replies are not stored."
      />
      {projects.loading && !projects.data ? <Spinner label="Loading your projects" /> : null}
      <ErrorNotice error={projects.error} onRetry={projects.reload} />

      {projects.data && !hasProjects ? (
        <EmptyState
          title="Create a project first"
          action={
            <Link to="/app/projects" className="btn btn--primary">
              Go to projects
            </Link>
          }
        >
          Playground requests are counted against a project, so you can see them in its request
          logs and usage.
        </EmptyState>
      ) : null}

      {hasProjects ? (
        <div className={`pg${detailsOpen ? "" : " pg--no-details"}`} data-view={mobileView}>
          <div className="pg-tabs" role="group" aria-label="Playground sections">
            {(["chat", "settings", "details"] as MobileView[]).map((view) => (
              <button
                key={view}
                type="button"
                className="pg-tabs__button"
                aria-pressed={mobileView === view}
                onClick={() => setMobileView(view)}
              >
                {view === "chat" ? "Conversation" : view === "settings" ? "Settings" : "Details"}
              </button>
            ))}
          </div>

          <section className="pg__settings panel" aria-labelledby={fieldId("settings-title")}>
            <div className="panel__header">
              <h2 id={fieldId("settings-title")} className="panel__title">
                Settings
              </h2>
              {status ? <StatusPill tone={status.tone}>{status.label}</StatusPill> : null}
            </div>
            <div className="panel__body pg-form">
              <Field id={fieldId("project")} label="Project">
                <select
                  id={fieldId("project")}
                  className="select"
                  value={projectId}
                  onChange={(event) => setProjectId(event.target.value)}
                >
                  {(projects.data ?? []).map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                id={fieldId("model")}
                label="Model"
                hint={
                  selectedModel
                    ? `${selectedModel.parameters ?? "Unknown size"}. ${selectedModel.languages.join(", ")}.`
                    : undefined
                }
              >
                <select
                  id={fieldId("model")}
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
                id={fieldId("language")}
                label="Language"
                hint="Recorded in logs and metrics. It does not change how the model answers."
              >
                <select
                  id={fieldId("language")}
                  className="select"
                  value={language}
                  onChange={(event) => setLanguage(event.target.value as LanguageCode | "")}
                >
                  <option value="">Not set</option>
                  {languages.map((item) => (
                    <option key={item.code} value={item.code}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                id={fieldId("system")}
                label="System instruction"
                hint="Optional. Sent first, before the conversation."
              >
                <textarea
                  id={fieldId("system")}
                  className="textarea pg-system"
                  value={system}
                  maxLength={4000}
                  onChange={(event) => setSystem(event.target.value)}
                />
              </Field>
              {PARAM_ORDER.map((field) => (
                <Field
                  key={field}
                  id={fieldId(field)}
                  label={PARAM_LABELS[field]}
                  hint={PARAM_HINTS[field]}
                  error={paramErrors[field]}
                >
                  <input
                    id={fieldId(field)}
                    className="input"
                    type="number"
                    inputMode="decimal"
                    min={PARAM_LIMITS[field].min}
                    max={PARAM_LIMITS[field].max}
                    step={PARAM_LIMITS[field].step}
                    value={fields[field]}
                    aria-invalid={paramErrors[field] ? "true" : undefined}
                    onChange={(event) => changeParam(field, event.target.value)}
                  />
                </Field>
              ))}
              <div>
                <button
                  type="button"
                  className="btn btn--secondary btn--sm"
                  onClick={() => {
                    setFields(toFields(DEFAULT_PARAMS));
                    setParamErrors({});
                  }}
                >
                  Reset parameters
                </button>
              </div>
              <p className="muted pg-note">
                Streaming is in development. A reply appears when generation finishes.
              </p>
            </div>
          </section>

          <section className="pg__chat panel" aria-labelledby={fieldId("chat-title")}>
            <div className="panel__header">
              <h2 id={fieldId("chat-title")} className="panel__title">
                Conversation
              </h2>
              <div className="pg-actions">
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={regenerate}
                  disabled={!canRegenerate}
                >
                  Regenerate
                </button>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={clearConversation}
                  disabled={sending || turns.length === 0}
                >
                  Clear
                </button>
                <CopyButton text={exportConversation(system, turns)} label="Copy chat" />
                {detailsOpen ? null : (
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm pg-desktop-only"
                    onClick={() => setDetailsOpen(true)}
                  >
                    Show details
                  </button>
                )}
              </div>
            </div>

            {selectedModel && selectedModel.status !== "available" ? (
              <div className="pg-banner notice notice--warning" role="status">
                <p className="notice__message">
                  The model host is not connected right now ({status?.label.toLowerCase()}).
                </p>
                <p className="muted">
                  Messages return a structured error until it is back. The dashboard shows the
                  same status.
                </p>
              </div>
            ) : null}

            <div
              className="pg-messages"
              ref={listRef}
              role="log"
              aria-live="polite"
              aria-label="Conversation"
              tabIndex={0}
            >
              {turns.length === 0 && !sending ? (
                <p className="muted pg-empty">
                  No messages yet. Write a prompt below and press Send. Use Ctrl+Enter as a
                  shortcut.
                </p>
              ) : null}
              {turns.map((turn) => (
                <article key={turn.id} className={`pg-msg pg-msg--${turn.role}`}>
                  <h3 className="pg-msg__role">{turn.role === "user" ? "You" : "N-ATLAS"}</h3>
                  <p className="pg-msg__body">{turn.content}</p>
                  {turn.meta ? (
                    <p className="pg-msg__meta">
                      <span>{formatNumber(turn.meta.totalTokens)} tokens</span>
                      <span>{formatLatency(turn.meta.roundTripMs)}</span>
                      <span>{describeFinish(turn.meta.finishReason)}</span>
                      <span>{turn.meta.requestId}</span>
                    </p>
                  ) : null}
                </article>
              ))}
              {sending ? (
                <div className="pg-wait">
                  <Spinner label="Waiting for N-ATLAS" />
                  <span className="muted">
                    Waiting for N-ATLAS. A long reply can take a minute.
                  </span>
                </div>
              ) : null}
            </div>

            <div className="pg-composer">
              <ErrorNotice error={error} />
              {notice ? (
                <p className="notice notice--info" role="status">
                  {notice}
                </p>
              ) : null}
              <Field id={fieldId("draft")} label="Message">
                <textarea
                  id={fieldId("draft")}
                  className="textarea pg-draft"
                  value={draft}
                  maxLength={32000}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                      event.preventDefault();
                      void send(draft, turns);
                    }
                  }}
                />
              </Field>
              <div className="pg-composer__actions">
                <button
                  type="button"
                  className="btn btn--primary"
                  onClick={() => void send(draft, turns)}
                  disabled={sending || !draft.trim() || !projectId}
                >
                  Send
                </button>
                {sending ? (
                  <button type="button" className="btn btn--secondary" onClick={stop}>
                    Stop
                  </button>
                ) : null}
              </div>
            </div>
          </section>

          <aside className="pg__details panel" aria-labelledby={fieldId("details-title")}>
            <div className="panel__header">
              <h2 id={fieldId("details-title")} className="panel__title">
                Details
              </h2>
              <div className="pg-actions">
                <div className="pg-seg" role="group" aria-label="Detail view">
                  {(["request", "response", "code"] as DetailTab[]).map((tab) => (
                    <button
                      key={tab}
                      type="button"
                      aria-pressed={detailTab === tab}
                      onClick={() => setDetailTab(tab)}
                    >
                      {tab === "request" ? "Request" : tab === "response" ? "Response" : "Code"}
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm pg-desktop-only"
                  onClick={() => setDetailsOpen(false)}
                >
                  Hide
                </button>
              </div>
            </div>
            <div className="panel__body pg-details">
              {detailTab === "request" ? (
                last ? (
                  <CodeBlock
                    label="Request body"
                    code={JSON.stringify(last.request, null, 2)}
                  />
                ) : (
                  <p className="muted">Send a message to see the exact request body.</p>
                )
              ) : null}

              {detailTab === "response" ? (
                last?.response ? (
                  <>
                    <dl className="kv pg-kv">
                      <dt>Request ID</dt>
                      <dd className="mono">{last.response.request_id}</dd>
                      <dt>Model</dt>
                      <dd className="mono">{last.response.model}</dd>
                      <dt>Finish</dt>
                      <dd>{describeFinish(last.response.finish_reason)}</dd>
                      <dt>Tokens</dt>
                      <dd>
                        {formatNumber(last.response.usage.prompt_tokens)} in,{" "}
                        {formatNumber(last.response.usage.completion_tokens)} out,{" "}
                        {formatNumber(last.response.usage.total_tokens)} total
                      </dd>
                      <dt>Gateway time</dt>
                      <dd>{formatLatency(last.response.latency_ms)}</dd>
                      <dt>Round trip</dt>
                      <dd>{formatLatency(last.roundTripMs)}</dd>
                    </dl>
                    <CodeBlock
                      label="Raw response"
                      code={JSON.stringify(last.response, null, 2)}
                    />
                  </>
                ) : last?.error ? (
                  <>
                    <p className="muted">The gateway returned an error.</p>
                    <CodeBlock label="Error response" code={errorBody(last.error)} />
                  </>
                ) : last?.cancelled ? (
                  <p className="muted">The request was stopped before a response arrived.</p>
                ) : (
                  <p className="muted">The raw response appears here after you send a message.</p>
                )
              ) : null}

              {detailTab === "code" ? (
                <>
                  <p className="muted pg-note">
                    The same request for your own application. It uses a project API key, which
                    you create on the{" "}
                    <Link to={`/app/projects/${projectId}`}>project page</Link>. The playground
                    itself needs no key.
                  </p>
                  <div className="pg-seg" role="group" aria-label="Code language">
                    {SNIPPET_KINDS.map((kind) => (
                      <button
                        key={kind.id}
                        type="button"
                        aria-pressed={snippetKind === kind.id}
                        onClick={() => setSnippetKind(kind.id)}
                      >
                        {kind.label}
                      </button>
                    ))}
                  </div>
                  <CodeBlock
                    label={SNIPPET_KINDS.find((kind) => kind.id === snippetKind)?.label ?? "Code"}
                    code={buildSnippet(snippetKind, snippetInput())}
                  />
                </>
              ) : null}
            </div>
          </aside>
        </div>
      ) : null}
    </>
  );
}
