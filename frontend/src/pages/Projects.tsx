import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { ErrorNotice } from "../components/ErrorNotice";
import { Modal } from "../components/Modal";
import { PageHeader } from "../components/PageHeader";
import { Spinner } from "../components/Spinner";
import { api } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { useAsyncData } from "../lib/useAsyncData";
import { useDocumentTitle } from "../lib/useDocumentTitle";

function CreateProjectModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const project = await api.projects.create(name, description);
      setName("");
      setDescription("");
      onClose();
      navigate(`/app/projects/${project.id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error("Could not create the project."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title="New project"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn--secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            form="create-project-form"
            className="btn btn--primary"
            disabled={busy || name.trim() === ""}
          >
            {busy ? "Creating" : "Create project"}
          </button>
        </>
      }
    >
      <form id="create-project-form" className="form" onSubmit={onSubmit}>
        <ErrorNotice error={error} />
        <div className="field">
          <label className="field__label" htmlFor="project-name">
            Name
          </label>
          <input
            id="project-name"
            className="input"
            maxLength={80}
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <div className="field">
          <label className="field__label" htmlFor="project-description">
            Description <span className="muted">(optional)</span>
          </label>
          <textarea
            id="project-description"
            className="textarea"
            maxLength={500}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </div>
      </form>
    </Modal>
  );
}

export function ProjectsPage() {
  useDocumentTitle("Projects");
  const { data, error, loading, reload } = useAsyncData(() => api.projects.list(), []);
  const [creating, setCreating] = useState(false);

  return (
    <>
      <PageHeader
        title="Projects"
        description="A project groups API keys, usage and logs for one application."
        actions={
          <button type="button" className="btn btn--primary" onClick={() => setCreating(true)}>
            New project
          </button>
        }
      />
      {loading && !data ? <Spinner label="Loading projects" /> : null}
      <ErrorNotice error={error} onRetry={reload} />
      {data && data.length === 0 ? (
        <EmptyState
          title="You have no projects yet"
          action={
            <button type="button" className="btn btn--primary" onClick={() => setCreating(true)}>
              Create a project
            </button>
          }
        >
          Create a project for each application you build on N-ATLAS. You can then issue API keys
          scoped to that project.
        </EmptyState>
      ) : null}
      {data && data.length > 0 ? (
        <div className="panel">
          <div className="panel__body panel__body--flush table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Description</th>
                  <th className="num">Active keys</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {data.map((project) => (
                  <tr key={project.id}>
                    <td>
                      <Link to={`/app/projects/${project.id}`}>{project.name}</Link>
                    </td>
                    <td className="muted">{project.description ?? "No description"}</td>
                    <td className="num">{project.active_key_count}</td>
                    <td>{formatDateTime(project.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
      <CreateProjectModal
        open={creating}
        onClose={() => {
          setCreating(false);
          reload();
        }}
      />
    </>
  );
}
