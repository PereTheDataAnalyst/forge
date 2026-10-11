import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { CodeBlock } from "../components/CodeBlock";
import { CopyButton } from "../components/CopyButton";
import { EmptyState } from "../components/EmptyState";
import { ErrorNotice } from "../components/ErrorNotice";
import { Modal } from "../components/Modal";
import { PageHeader } from "../components/PageHeader";
import { Spinner } from "../components/Spinner";
import { StatusPill, type Tone } from "../components/StatusPill";
import { DEFAULT_MODEL_ID, selectableScopes } from "../config";
import { api } from "../lib/api";
import { formatDateTime, formatRelative } from "../lib/format";
import { buildSnippet } from "../lib/snippets";
import type { ApiKey, CreatedApiKey, KeyStatus } from "../lib/types";
import { useAsyncData } from "../lib/useAsyncData";
import { useDocumentTitle } from "../lib/useDocumentTitle";

type Dialog =
  | { type: "none" }
  | { type: "create" }
  | { type: "rotate"; key: ApiKey }
  | { type: "revoke"; key: ApiKey }
  | { type: "archive" };

const STATUS_TONE: Record<KeyStatus, { tone: Tone; label: string }> = {
  active: { tone: "success", label: "Active" },
  revoked: { tone: "neutral", label: "Revoked" },
  expired: { tone: "warning", label: "Expired" },
};

function toError(caught: unknown, fallback: string): Error {
  return caught instanceof Error ? caught : new Error(fallback);
}

function SecretDialog({ secret, onDone }: { secret: CreatedApiKey | null; onDone: () => void }) {
  return (
    <Modal
      open={secret !== null}
      title="Copy your API key"
      persistent
      onClose={onDone}
      footer={
        <button type="button" className="btn btn--primary" onClick={onDone}>
          I have stored the key
        </button>
      }
    >
      {secret ? (
        <>
          <div className="notice notice--warning">
            <p className="notice__message">This is the only time the full key is shown.</p>
            <p>
              Store it in a secret manager or an environment variable. If you lose it, revoke it
              and create a new one.
            </p>
          </div>
          <CodeBlock label={secret.name} code={secret.key} />
        </>
      ) : null}
    </Modal>
  );
}

function CreateKeyModal({
  open,
  projectId,
  onClose,
  onCreated,
}: {
  open: boolean;
  projectId: string;
  onClose: () => void;
  onCreated: (key: CreatedApiKey) => void;
}) {
  const [name, setName] = useState("Server key");
  const [expiry, setExpiry] = useState("never");
  const [scopes, setScopes] = useState<string[]>(selectableScopes.map((scope) => scope.id));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  function toggle(scope: string) {
    setScopes((current) =>
      current.includes(scope) ? current.filter((item) => item !== scope) : [...current, scope],
    );
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.keys.create(projectId, {
        name: name.trim(),
        scopes,
        expires_in_days: expiry === "never" ? undefined : Number(expiry),
      });
      onClose();
      onCreated(created);
    } catch (caught) {
      setError(toError(caught, "Could not create the key."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title="Create an API key"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn--secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            form="create-key-form"
            className="btn btn--primary"
            disabled={busy || name.trim() === "" || scopes.length === 0}
          >
            {busy ? "Creating" : "Create key"}
          </button>
        </>
      }
    >
      <form id="create-key-form" className="form" onSubmit={onSubmit}>
        <ErrorNotice error={error} />
        <div className="field">
          <label className="field__label" htmlFor="key-name">
            Name
          </label>
          <input
            id="key-name"
            className="input"
            maxLength={80}
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <span className="field__hint">A label that helps you recognise where the key is used.</span>
        </div>
        <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="field__label">Scopes</legend>
          <div className="checks">
            {selectableScopes.map((scope) => (
              <label key={scope.id} className="check">
                <input
                  type="checkbox"
                  checked={scopes.includes(scope.id)}
                  onChange={() => toggle(scope.id)}
                />
                <span>
                  <span className="mono">{scope.label}</span>
                  <br />
                  <span className="muted">{scope.description}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        <div className="field">
          <label className="field__label" htmlFor="key-expiry">
            Expiry
          </label>
          <select
            id="key-expiry"
            className="select"
            value={expiry}
            onChange={(event) => setExpiry(event.target.value)}
          >
            <option value="never">No expiry</option>
            <option value="30">30 days</option>
            <option value="90">90 days</option>
            <option value="365">1 year</option>
          </select>
        </div>
      </form>
    </Modal>
  );
}

function RotateKeyModal({
  apiKey,
  projectId,
  onClose,
  onRotated,
}: {
  apiKey: ApiKey | null;
  projectId: string;
  onClose: () => void;
  onRotated: (key: CreatedApiKey) => void;
}) {
  const [grace, setGrace] = useState("0");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  async function rotate() {
    if (!apiKey) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.keys.rotate(projectId, apiKey.id, Number(grace));
      onClose();
      onRotated(result.new_key);
    } catch (caught) {
      setError(toError(caught, "Could not rotate the key."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={apiKey !== null}
      title="Rotate API key"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn--secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn btn--primary" onClick={rotate} disabled={busy}>
            {busy ? "Rotating" : "Rotate key"}
          </button>
        </>
      }
    >
      <ErrorNotice error={error} />
      <p>
        A new key with the same name and scopes is issued for <strong>{apiKey?.name}</strong>. Update
        your application with the new key, then the old one stops working.
      </p>
      <div className="field">
        <label className="field__label" htmlFor="grace">
          Old key
        </label>
        <select
          id="grace"
          className="select"
          value={grace}
          onChange={(event) => setGrace(event.target.value)}
        >
          <option value="0">Stop working immediately</option>
          <option value="600">Keep working for 10 minutes</option>
          <option value="3600">Keep working for 1 hour</option>
          <option value="86400">Keep working for 24 hours</option>
        </select>
      </div>
    </Modal>
  );
}

function ConfirmModal({
  open,
  title,
  confirmLabel,
  busy,
  error,
  onConfirm,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  confirmLabel: string;
  busy: boolean;
  error: Error | null;
  onConfirm: () => void;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <Modal
      open={open}
      title={title}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn--secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn btn--danger" onClick={onConfirm} disabled={busy}>
            {busy ? "Working" : confirmLabel}
          </button>
        </>
      }
    >
      <ErrorNotice error={error} />
      {children}
    </Modal>
  );
}

export function ProjectDetailPage() {
  const { projectId = "" } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const project = useAsyncData(() => api.projects.get(projectId), [projectId]);
  const keys = useAsyncData(() => api.keys.list(projectId), [projectId]);
  const [dialog, setDialog] = useState<Dialog>({ type: "none" });
  const [secret, setSecret] = useState<CreatedApiKey | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<Error | null>(null);

  useDocumentTitle(project.data?.name ?? "Project");

  function closeDialog() {
    setDialog({ type: "none" });
    setActionError(null);
  }

  async function runAction(action: () => Promise<void>) {
    setBusy(true);
    setActionError(null);
    try {
      await action();
      closeDialog();
    } catch (caught) {
      setActionError(toError(caught, "The action failed."));
    } finally {
      setBusy(false);
    }
  }

  if (project.loading && !project.data) return <Spinner label="Loading the project" />;
  if (project.error) {
    return (
      <>
        <ErrorNotice error={project.error} onRetry={project.reload} />
        <Link to="/app/projects">Back to projects</Link>
      </>
    );
  }
  if (!project.data) return null;
  const current = project.data;

  const origin = window.location.origin;
  const chatExample = buildSnippet("curl", {
    baseUrl: origin,
    model: DEFAULT_MODEL_ID,
    prompt: "Write a one sentence greeting for a new customer.",
    language: "en",
    temperature: 0.1,
    maxTokens: 128,
  });
  const modelsExample = [
    `curl '${origin}/api/v1/models' \\`,
    `  -H "Authorization: Bearer $FORGE_API_KEY"`,
  ].join("\n");

  return (
    <>
      <PageHeader
        title={current.name}
        description={current.description ?? "No description."}
        actions={
          <>
            <button type="button" className="btn btn--primary" onClick={() => setDialog({ type: "create" })}>
              Create API key
            </button>
            <button type="button" className="btn btn--secondary" onClick={() => setDialog({ type: "archive" })}>
              Archive project
            </button>
          </>
        }
      />

      <section className="panel" aria-labelledby="overview-title">
        <div className="panel__header">
          <h2 id="overview-title" className="panel__title">
            Overview
          </h2>
        </div>
        <div className="panel__body">
          <dl className="kv">
            <dt>Project ID</dt>
            <dd>
              <span className="mono">{current.id}</span> <CopyButton text={current.id} />
            </dd>
            <dt>Created</dt>
            <dd>{formatDateTime(current.created_at)}</dd>
            <dt>Active keys</dt>
            <dd>{current.active_key_count}</dd>
          </dl>
        </div>
      </section>

      <section className="panel" aria-labelledby="keys-title">
        <div className="panel__header">
          <h2 id="keys-title" className="panel__title">
            API keys
          </h2>
        </div>
        {keys.loading && !keys.data ? (
          <div className="panel__body">
            <Spinner label="Loading keys" />
          </div>
        ) : null}
        {keys.error ? (
          <div className="panel__body">
            <ErrorNotice error={keys.error} onRetry={keys.reload} />
          </div>
        ) : null}
        {keys.data && keys.data.length === 0 ? (
          <div className="panel__body">
            <EmptyState
              title="No API keys yet"
              action={
                <button type="button" className="btn btn--primary" onClick={() => setDialog({ type: "create" })}>
                  Create the first key
                </button>
              }
            >
              Keys authenticate requests to the gateway. The full key is shown once, when you create
              it.
            </EmptyState>
          </div>
        ) : null}
        {keys.data && keys.data.length > 0 ? (
          <div className="panel__body panel__body--flush table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Key</th>
                  <th>Scopes</th>
                  <th>Status</th>
                  <th>Last used</th>
                  <th>Expires</th>
                  <th>
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {keys.data.map((key) => {
                  const status = STATUS_TONE[key.status];
                  return (
                    <tr key={key.id}>
                      <td>{key.name}</td>
                      <td className="mono">{key.prefix}…</td>
                      <td className="mono">{key.scopes.join(", ")}</td>
                      <td>
                        <StatusPill tone={status.tone}>{status.label}</StatusPill>
                      </td>
                      <td>{formatRelative(key.last_used_at)}</td>
                      <td>{key.expires_at ? formatDateTime(key.expires_at) : "Never"}</td>
                      <td>
                        {key.status === "active" ? (
                          <div className="table__actions">
                            <button
                              type="button"
                              className="btn btn--ghost btn--sm"
                              onClick={() => setDialog({ type: "rotate", key })}
                            >
                              Rotate
                            </button>
                            <button
                              type="button"
                              className="btn btn--ghost btn--sm"
                              onClick={() => setDialog({ type: "revoke", key })}
                            >
                              Revoke
                            </button>
                          </div>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <section className="panel" aria-labelledby="quickstart-title">
        <div className="panel__header">
          <h2 id="quickstart-title" className="panel__title">
            Try your key
          </h2>
          <Link to="/docs#endpoints" className="btn btn--ghost btn--sm">
            API reference
          </Link>
        </div>
        <div className="panel__body" style={{ display: "grid", gap: 14 }}>
          <p className="muted">
            Set <code>FORGE_API_KEY</code> to the key you copied, then call the gateway.
          </p>
          <CodeBlock label="List models" code={modelsExample} />
          <CodeBlock label="Generate a reply" code={chatExample} />
        </div>
      </section>

      <CreateKeyModal
        open={dialog.type === "create"}
        projectId={current.id}
        onClose={closeDialog}
        onCreated={(created) => {
          setSecret(created);
          keys.reload();
          project.reload();
        }}
      />
      <RotateKeyModal
        apiKey={dialog.type === "rotate" ? dialog.key : null}
        projectId={current.id}
        onClose={closeDialog}
        onRotated={(created) => {
          setSecret(created);
          keys.reload();
          project.reload();
        }}
      />
      <ConfirmModal
        open={dialog.type === "revoke"}
        title="Revoke API key"
        confirmLabel="Revoke key"
        busy={busy}
        error={actionError}
        onClose={closeDialog}
        onConfirm={() => {
          if (dialog.type !== "revoke") return;
          const target = dialog.key;
          void runAction(async () => {
            await api.keys.revoke(current.id, target.id);
            keys.reload();
            project.reload();
          });
        }}
      >
        <p>
          Requests that use <strong>{dialog.type === "revoke" ? dialog.key.name : ""}</strong> will
          fail immediately. This cannot be undone.
        </p>
      </ConfirmModal>
      <ConfirmModal
        open={dialog.type === "archive"}
        title="Archive project"
        confirmLabel="Archive project"
        busy={busy}
        error={actionError}
        onClose={closeDialog}
        onConfirm={() =>
          void runAction(async () => {
            await api.projects.archive(current.id);
            navigate("/app/projects", { replace: true });
          })
        }
      >
        <p>
          Archiving <strong>{current.name}</strong> hides it from your project list and stops all of
          its API keys from working.
        </p>
      </ConfirmModal>
      <SecretDialog secret={secret} onDone={() => setSecret(null)} />
    </>
  );
}
