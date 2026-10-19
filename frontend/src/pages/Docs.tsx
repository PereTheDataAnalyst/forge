import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { CodeBlock } from "../components/CodeBlock";
import { site } from "../config";
import { RESPONSE_SHAPE } from "../lib/snippets";
import { useDocumentTitle } from "../lib/useDocumentTitle";

const toc: [string, string][] = [
  ["quick-start", "Quick start"],
  ["authentication", "Authentication"],
  ["endpoints", "Endpoints"],
  ["web-app", "Playground and Evaluations"],
  ["errors", "Errors"],
  ["rate-limits", "Rate limits"],
  ["connect-n-atlas", "Connect N-ATLAS"],
  ["licensing", "Licensing and attribution"],
];

const errorCodes: [string, string, string][] = [
  ["missing_api_key", "401", "No Authorization header was sent."],
  ["invalid_api_key", "401", "The key is malformed, unknown, or the header is not a Bearer token."],
  ["api_key_revoked", "401", "The key was revoked or rotated."],
  ["api_key_expired", "401", "The key passed its expiry date."],
  ["insufficient_scope", "403", "The key lacks the scope this endpoint needs."],
  ["project_archived", "403", "The project that owns the key is archived."],
  ["validation_error", "422", "The body does not match the schema. See details for each field."],
  ["invalid_messages", "422", "The message list breaks a rule, for example the last role is not user."],
  ["invalid_parameters", "422", "A parameter is outside the allowed range for this deployment."],
  ["input_too_long", "422", "The combined message length exceeds the configured limit."],
  ["context_length_exceeded", "422", "The prompt plus max_tokens does not fit the model context."],
  ["model_not_found", "404", "The requested model is not available through this gateway."],
  ["payload_too_large", "413", "The request body is larger than the configured limit."],
  ["rate_limited", "429", "The key exceeded its per-minute limit. Retry after the delay in Retry-After."],
  ["provider_not_configured", "503", "No N-ATLAS inference worker is connected to this deployment."],
  ["provider_unavailable", "503", "The inference worker is not reachable or its model failed to load."],
  ["provider_loading", "503", "The model is still loading. Retry after the delay in Retry-After."],
  ["provider_busy", "503", "The worker is at capacity. Retry after the delay in Retry-After."],
  ["provider_timeout", "504", "The worker did not answer in time."],
  ["provider_misconfigured", "502", "The worker rejected the gateway credentials."],
  ["provider_invalid_response", "502", "The worker returned a response the gateway could not validate."],
  ["provider_error", "502", "The worker returned an unexpected error."],
  ["internal_error", "500", "Something failed inside Forge. Quote the request ID when you report it."],
];

const parameters: [string, string, string][] = [
  ["messages", "required", "1 to 100 messages with role system, user or assistant. A system message is only allowed first. The last message must have the role user."],
  ["model", "optional", "A model id from GET /api/v1/models. Defaults to the configured model."],
  ["max_tokens", "optional", "1 to 8192, default 512. Deployments cap it, 2048 by default."],
  ["temperature", "optional", "0 to 2, default 0.1. A value of 0 uses greedy decoding."],
  ["top_p", "optional", "Greater than 0 and at most 1, default 1.0. Ignored when temperature is 0."],
  ["repetition_penalty", "optional", "1 to 2, default 1.12."],
  ["language", "optional", "en, ha, ig or yo. Recorded in request logs and metrics. It does not change model behaviour."],
];

const modelsResponse = `{
  "data": [
    {
      "id": "NCAIR1/N-ATLaS",
      "kind": "llm",
      "display_name": "N-ATLaS LLM",
      "architecture": "LlamaForCausalLM (Llama-3 8B base)",
      "parameters": "8B",
      "languages": ["en", "ha", "ig", "yo"],
      "context_length_tokens": 8092,
      "license": "N-ATLaS Terms of Use (Open-Source Research and Innovation License)",
      "source_url": "https://huggingface.co/NCAIR1/N-ATLaS",
      "attribution": "N-ATLaS is an initiative of the Federal Ministry of Communications, Innovation and Digital Economy, and powered by Awarri Technologies.",
      "status": "available"
    }
  ]
}`;

const errorShape = `{
  "error": {
    "code": "validation_error",
    "message": "The request is not valid. See details for the fields to fix.",
    "request_id": "req_3f9a1c0d5b7e2a4c6d8e0f12",
    "details": [{ "field": "messages", "message": "List should have at least 1 item" }]
  }
}`;

const workerCommands = `cd inference\\natlas-worker
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
# Install PyTorch for your hardware first: https://pytorch.org/get-started/locally/
pip install -r requirements-model.txt
Copy-Item .env.example .env   # then set NATLAS_WORKER_TOKEN and HF_TOKEN
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8081`;

const connectCommands = `# in the repository root
.\\scripts\\connect-worker.ps1 -Url http://127.0.0.1:8081
# restart the backend, then confirm:
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/ready`;

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="doc-section" aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`}>{title}</h2>
      {children}
    </section>
  );
}

function Endpoint({
  method,
  path,
  scope,
  children,
}: {
  method: "GET" | "POST";
  path: string;
  scope?: string;
  children: ReactNode;
}) {
  return (
    <div className="endpoint">
      <div className="endpoint__head">
        <span className={`method method--${method.toLowerCase()}`}>{method}</span>
        <code>{path}</code>
        {scope ? (
          <span className="endpoint__scope">
            Scope <code>{scope}</code>
          </span>
        ) : null}
      </div>
      <div className="endpoint__body">{children}</div>
    </div>
  );
}

export function DocsPage() {
  useDocumentTitle("Documentation");
  const origin = window.location.origin;
  const modelsCall = `curl '${origin}/api/v1/models' \\\n  -H "Authorization: Bearer $FORGE_API_KEY"`;

  return (
    <div className="container docs">
      <nav className="docs__toc" aria-label="On this page">
        <p className="docs__toc-title">Documentation</p>
        <ul>
          {toc.map(([id, label]) => (
            <li key={id}>
              <a href={`#${id}`}>{label}</a>
            </li>
          ))}
        </ul>
      </nav>

      <article className="docs__content">
        <h1>N-ATLAS Forge documentation</h1>
        <p className="docs__lead">
          This reference covers what exists today. Endpoints that are not listed here are not part of
          the API. All paths are relative to your Forge host and start with <code>/api/v1</code>.
        </p>

        <Section id="quick-start" title="Quick start">
          <ol className="steps">
            <li>
              <Link to="/register">Create an account</Link>, then create a project from the
              dashboard.
            </li>
            <li>Create an API key for the project and copy it. The full key is shown once.</li>
            <li>
              Export it as <code>FORGE_API_KEY</code> and call the gateway.
            </li>
          </ol>
          <CodeBlock label="List models" code={modelsCall} />
        </Section>

        <Section id="authentication" title="Authentication">
          <p>
            Gateway endpoints authenticate with a project API key sent as a Bearer token. Keys look
            like <code>nfk_</code> followed by a public identifier and a secret. Each key carries
            scopes, and a request needs the scope its endpoint lists.
          </p>
          <CodeBlock label="Header" code={"Authorization: Bearer nfk_<identifier>_<secret>"} />
          <p>
            The dashboard and key management endpoints use cookie sessions and CSRF tokens. They are
            meant for the web application, not for programmatic use. Keys never belong in URLs.
          </p>
        </Section>

        <Section id="endpoints" title="Endpoints">
          <Endpoint method="GET" path="/api/v1/health/live">
            <p>
              Liveness probe. Needs no authentication. Returns <code>{`{"status": "ok"}`}</code> and
              the version.
            </p>
          </Endpoint>
          <Endpoint method="GET" path="/api/v1/health/ready">
            <p>
              Readiness probe. Needs no authentication. Reports the database, Redis and the N-ATLAS
              provider. It returns 503 only when the database or Redis is down. An unconnected
              provider is reported as <code>degraded</code>.
            </p>
          </Endpoint>
          <Endpoint method="GET" path="/api/v1/models" scope="models.read">
            <p>Lists the models available through this gateway and their current status.</p>
            <CodeBlock label="200 OK" code={modelsResponse} />
            <p>
              <code>status</code> is one of <code>available</code>, <code>loading</code>,{" "}
              <code>not_configured</code> or <code>unavailable</code>.
            </p>
          </Endpoint>
          <Endpoint method="POST" path="/api/v1/chat" scope="chat.create">
            <p>Generates a reply from N-ATLAS for a conversation.</p>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Field</th>
                    <th>Use</th>
                    <th>Description</th>
                  </tr>
                </thead>
                <tbody>
                  {parameters.map(([name, use, description]) => (
                    <tr key={name}>
                      <td className="mono">{name}</td>
                      <td>{use}</td>
                      <td>{description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p>
              Unknown fields are rejected. The defaults for temperature and repetition penalty mirror
              the example in the official model card.
            </p>
            <CodeBlock label="200 OK" code={RESPONSE_SHAPE} />
            <p>
              <code>finish_reason</code> is <code>stop</code> when the model ended its turn and{" "}
              <code>length</code> when generation hit the token or time limit.{" "}
              <code>prompt_tokens</code> counts the prompt after chat templating and{" "}
              <code>completion_tokens</code> excludes the end-of-turn token. Every response carries
              an <code>X-Request-ID</code> header and rate limit headers.
            </p>
          </Endpoint>
        </Section>

        <Section id="web-app" title="Playground and Evaluations">
          <p>
            The web app has two tools that use the same gateway as your API keys. They need a
            signed-in session and are pages in the app, not endpoints for programmatic use.
          </p>
          <ul className="bullets">
            <li>
              <strong>Playground.</strong> Try prompts with your own parameters, keep a
              conversation going, and read the exact request and response. Requests are recorded
              under one of your projects and appear in its request logs. Prompts and replies are
              not stored.
            </li>
            <li>
              <strong>Evaluation Lab.</strong> Add a dataset of prompts with checks, run it against
              the model and read which checks held. Results export as CSV or JSON. The Lab stores
              your datasets and the replies from your runs. There is no accuracy score and no
              model-based judge.
            </li>
          </ul>
          <p>
            Sign in, then open <Link to="/app/playground">Playground</Link> or{" "}
            <Link to="/app/evaluations">Evaluations</Link>.
          </p>
        </Section>

        <Section id="errors" title="Errors">
          <p>Every error uses the same shape and includes the request ID for support.</p>
          <CodeBlock label="Error response" code={errorShape} />
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Code</th>
                  <th>HTTP</th>
                  <th>Meaning</th>
                </tr>
              </thead>
              <tbody>
                {errorCodes.map(([code, status, meaning]) => (
                  <tr key={code}>
                    <td className="mono">{code}</td>
                    <td>{status}</td>
                    <td>{meaning}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>

        <Section id="rate-limits" title="Rate limits">
          <p>
            Each API key may send 60 requests per minute by default, counted in a fixed one-minute
            window. Operators change this with <code>GATEWAY_RATE_LIMIT_PER_MINUTE</code>. Responses
            include <code>X-RateLimit-Limit</code>, <code>X-RateLimit-Remaining</code> and{" "}
            <code>X-RateLimit-Reset</code>. A limited request returns 429 with{" "}
            <code>Retry-After</code>. Sign-in and registration are limited per client address, and
            five failed sign-ins lock an account for 15 minutes by default.
          </p>
        </Section>

        <Section id="connect-n-atlas" title="Connect N-ATLAS">
          <p>
            The official N-ATLAS release is open weights on Hugging Face, and the model page states
            that no inference provider hosts it. Forge therefore ships an inference worker that runs
            the model with the Transformers library exactly as the model card shows, and serves it to
            the gateway over a private, authenticated API. Forge never returns generated text unless
            the real model produced it.
          </p>
          <ol className="steps">
            <li>
              Open the{" "}
              <a href={site.modelCardUrl} rel="noreferrer">
                model page
              </a>
              , accept the terms, and create a Hugging Face access token with read permission.
            </li>
            <li>
              Use a machine that can hold the model. At 16-bit precision an 8 billion parameter model
              needs about 16 GB for the weights alone, so a GPU with more than that is the practical
              choice.
            </li>
            <li>Start the worker, then point the gateway at it.</li>
          </ol>
          <CodeBlock label="Start the worker (PowerShell)" code={workerCommands} />
          <CodeBlock label="Connect the gateway (PowerShell)" code={connectCommands} />
          <p>
            Official N-ATLAS API credentials are issued to shortlisted teams of the National AI
            Innovation Challenge. That interface has not been published, so Forge does not include a
            client for it. The provider layer is separate from the gateway so a client can be added
            without changing the API.
          </p>
        </Section>

        <Section id="licensing" title="Licensing and attribution">
          <p>
            Use of N-ATLAS is governed by the{" "}
            <a href={site.modelCardUrl} rel="noreferrer">
              N-ATLaS Terms of Use
            </a>
            , not by the licence of this software. Read them before you deploy. In particular:
          </p>
          <ul className="bullets">
            <li>Public use must display the required attribution text shown in the page footer.</li>
            <li>
              The terms set a limit of 1,000 active end users before a commercial licence is needed.
            </li>
            <li>Derivative works must be released under the same terms.</li>
            <li>The terms list prohibited uses that apply to everything built with the model.</li>
          </ul>
        </Section>
      </article>
    </div>
  );
}
