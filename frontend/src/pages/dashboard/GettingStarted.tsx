import { Link } from "react-router-dom";
import type { DashboardSummary } from "../../lib/types";

interface Step {
  id: string;
  title: string;
  text: string;
  done: boolean;
  to: string;
  action: string;
}

/** Every step is computed from data the dashboard already loaded. Nothing here is stored separately. */
function buildSteps(summary: DashboardSummary): Step[] {
  const { totals } = summary;
  return [
    {
      id: "project",
      title: "Create a project",
      text: "Projects group API keys, usage and logs.",
      done: totals.projects > 0,
      to: "/app/projects",
      action: "Open projects",
    },
    {
      id: "key",
      title: "Create an API key",
      text: "The full key is shown once, so store it safely.",
      done: totals.active_api_keys > 0,
      to: "/app/projects",
      action: "Open projects",
    },
    {
      id: "request",
      title: "Send your first request",
      text: "Use the Playground, or call POST /api/v1/chat with your key. The request appears under Recent requests.",
      done: totals.requests > 0 || summary.recent_requests.length > 0,
      to: "/app/playground",
      action: "Open the Playground",
    },
    {
      id: "evaluation",
      title: "Run an evaluation",
      text: "Check N-ATLAS against your own rules, starting from an example dataset.",
      done: summary.recent_evaluation_runs.length > 0,
      to: "/app/evaluations",
      action: "Open Evaluations",
    },
  ];
}

/** Shows until every step is done, then disappears so the dashboard stays about activity. */
export function GettingStarted({ summary }: { summary: DashboardSummary }) {
  const steps = buildSteps(summary);
  const done = steps.filter((step) => step.done).length;
  if (done === steps.length) return null;

  return (
    <section className="panel" aria-labelledby="getting-started-title">
      <div className="panel__header">
        <h2 id="getting-started-title" className="panel__title">
          Get started
        </h2>
        <span className="muted">
          {done} of {steps.length} done
        </span>
      </div>
      <ol className="checklist">
        {steps.map((step) => (
          <li
            key={step.id}
            className={step.done ? "checklist__item checklist__item--done" : "checklist__item"}
          >
            <span className="checklist__mark" aria-hidden="true" />
            <div className="checklist__body">
              <p className="checklist__title">
                <span className="visually-hidden">{step.done ? "Done: " : "To do: "}</span>
                {step.title}
              </p>
              <p className="checklist__text">{step.text}</p>
            </div>
            {step.done ? null : (
              <Link to={step.to} className="btn btn--secondary btn--sm">
                {step.action}
              </Link>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
