import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { StatusPill } from "../components/StatusPill";
import { contactHref, languages, layersByStatus, site, type Layer } from "../config";
import { securityControls, securityDisclaimer } from "../content/security";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { LiveStatus } from "./landing/LiveStatus";
import { RequestConsole } from "./landing/RequestConsole";

interface Entry {
  title: string;
  text: ReactNode;
}

const steps: Entry[] = [
  {
    title: "Create an account",
    text: "Sign up with an email address and a password of at least 10 characters.",
  },
  {
    title: "Create a project and a key",
    text: "A project groups the keys, usage and logs of one application. A key belongs to one project, is shown once, and is stored only as a keyed hash.",
  },
  {
    title: "Send your first request",
    text: (
      <>
        Post to <code>/api/v1/chat</code> with the key as a Bearer token, or type a prompt in the
        Playground. Every response carries a request ID.
      </>
    ),
  },
  {
    title: "Check the answers and watch the traffic",
    text: "Run your own test cases in the Evaluation Lab, and follow requests, failures and latency on the dashboard.",
  },
];

const expectations: Entry[] = [
  {
    title: "The model host is not always on",
    text: (
      <>
        N-ATLAS runs on a separate GPU host that is not always connected, and there is no service
        level agreement yet. When the host is away, chat requests return a{" "}
        <code>provider_unavailable</code> error and the rest of Forge keeps working. Plan a
        fallback for anything critical.
      </>
    ),
  },
  {
    title: "The N-ATLaS terms apply to what you build",
    text: "Attribution is required, and the terms set a limit of 1,000 active end users before a commercial licence is needed. Forge cannot see your end users, so it cannot track that limit for you.",
  },
  {
    title: "What Forge stores",
    text: (
      <>
        Request logs hold metadata only: project, endpoint, status, latency, model, language and
        token counts. The Evaluation Lab stores the datasets you add and the replies from your
        runs. Read the <Link to="/privacy">privacy summary</Link>.
      </>
    ),
  },
  {
    title: "Accounts are basic",
    text: "Email verification, password reset, two-factor sign-in and team access are not available yet. Each project belongs to one owner.",
  },
  {
    title: "Limits",
    text: (
      <>
        Each API key may send 60 requests per minute by default. The{" "}
        <Link to="/docs#rate-limits">documentation</Link> lists every limit and error code.
      </>
    ),
  },
  {
    title: "Support",
    text: (
      <>
        To report a problem or ask a question,{" "}
        <a href={contactHref} rel="noreferrer">
          contact {site.company}
        </a>{" "}
        and include the request ID from the response.
      </>
    ),
  },
];

function LayerTable({ heading, rows }: { heading: string; rows: Layer[] }) {
  return (
    <div>
      <h3 className="layers__heading">{heading}</h3>
      <div className="table-wrap layers__table">
        <table className="table" role="table">
          <caption className="visually-hidden">{heading}</caption>
          <thead role="rowgroup">
            <tr role="row">
              <th scope="col" role="columnheader">
                Capability
              </th>
              <th scope="col" role="columnheader">
                What it does
              </th>
              <th scope="col" role="columnheader">
                Status
              </th>
            </tr>
          </thead>
          <tbody role="rowgroup">
            {rows.map((layer) => (
              <tr key={layer.name} role="row">
                <th scope="row" className="layers__name" role="rowheader">
                  {layer.name}
                </th>
                <td role="cell">{layer.summary}</td>
                <td role="cell">
                  <StatusPill tone={layer.status === "available" ? "success" : "neutral"}>
                    {layer.status === "available" ? "Available" : "In development"}
                  </StatusPill>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function LandingPage() {
  useDocumentTitle();
  const { status } = useAuth();
  const signedIn = status === "authenticated";

  const primaryAction = signedIn ? (
    <Link to="/app" className="btn btn--primary btn--lg">
      Open your dashboard
    </Link>
  ) : (
    <Link to="/register" className="btn btn--primary btn--lg">
      Create an account
    </Link>
  );

  return (
    <>
      <section className="hero">
        <div className="container hero__grid">
          <div className="hero__copy">
            <h1 className="hero__title">Call N-ATLAS from your app.</h1>
            <p className="hero__lead">
              Forge is a hosted API for N-ATLAS, the open language model for English, Hausa, Igbo
              and Yoruba. Create a project, generate a key and send requests from your own app. Try
              prompts in the Playground, check the answers against your own rules, and see
              requests, failures and latency on your dashboard.
            </p>
            <div className="hero__actions">
              {primaryAction}
              <Link to="/docs" className="btn btn--secondary btn--lg">
                Read the documentation
              </Link>
            </div>
            <div className="hero__status">
              <LiveStatus />
            </div>
            <p className="hero__meta">
              Forge is an independent project. NCAIR publishes the N-ATLAS weights under the
              N-ATLaS Terms of Use.
            </p>
          </div>
          <h2 className="visually-hidden">Request builder</h2>
          <RequestConsole />
        </div>
      </section>

      <section className="section section--muted" id="platform" aria-labelledby="platform-title">
        <div className="container">
          <h2 id="platform-title" className="section__title">
            What you can use today
          </h2>
          <p className="section__intro">
            Each capability shows its real status. Available means it runs on this site now. In
            development means you cannot use it yet.
          </p>

          <ol className="path" aria-label="Request path">
            <li>
              <span className="path__name">Your application</span>
              <span className="path__note">Sends a request with a project API key</span>
            </li>
            <li>
              <span className="path__name">Forge gateway</span>
              <span className="path__note">
                Authenticates, rate limits, validates, records metrics
              </span>
            </li>
            <li>
              <span className="path__name">N-ATLAS inference</span>
              <span className="path__note">The official model, run on a private worker</span>
            </li>
          </ol>

          <div className="layers">
            <LayerTable heading="Available now" rows={layersByStatus.available} />
            <LayerTable heading="In development" rows={layersByStatus.inDevelopment} />
          </div>
        </div>
      </section>

      <section className="section" id="start" aria-labelledby="start-title">
        <div className="container split">
          <div className="split__lead">
            <h2 id="start-title" className="section__title">
              Start in four steps
            </h2>
            <p>The same path works for a quick test and for an application you ship.</p>
          </div>
          <ol className="flow">
            {steps.map((step) => (
              <li key={step.title}>
                <h3>{step.title}</h3>
                <p>{step.text}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="section section--muted" id="tools" aria-labelledby="tools-title">
        <div className="container split">
          <div className="split__lead">
            <h2 id="tools-title" className="section__title">
              Check quality before you build around it
            </h2>
            <p>
              Quality differs between languages, and a model that answers well once can still fail
              your use case. Two tools in the web app let you find out with your own prompts.
            </p>
          </div>
          <dl className="controls controls--single">
            <div className="controls__item">
              <dt>Playground</dt>
              <dd>
                Try prompts in the browser with your own temperature, top p, maximum tokens and
                repetition penalty. Keep a conversation going, stop or regenerate a reply, and read
                the exact request, the raw response and the request ID. Prompts and replies are not
                stored.
              </dd>
            </div>
            <div className="controls__item">
              <dt>Evaluation Lab</dt>
              <dd>
                Write checks for your own prompts, or start from two example datasets. A run sends
                every case through the gateway and reports which checks held, with a breakdown by
                language and category, latency and each reply. Results export as CSV or JSON.
                Scores come from your rules: there is no accuracy score and no model-based judge.
              </dd>
            </div>
          </dl>
        </div>
      </section>

      <section className="section" id="n-atlas" aria-labelledby="n-atlas-title">
        <div className="container split">
          <div className="split__lead">
            <h2 id="n-atlas-title" className="section__title">
              What N-ATLAS is
            </h2>
            <p>
              N-ATLAS is Nigeria&apos;s open multilingual language model initiative. The text model
              is based on Llama-3 8B and works in English, Hausa, Igbo and Yoruba.
            </p>
            <p>
              The National Centre for Artificial Intelligence and Robotics (NCAIR) publishes the
              weights on Hugging Face under the N-ATLaS Terms of Use. The model cards describe
              running the model yourself. They do not describe a public hosted API. Forge connects
              to a running N-ATLAS host and adds what every application needs around it: keys,
              limits, logs and a stable API.
            </p>
          </div>
          <dl className="facts">
            <div>
              <dt>Text model</dt>
              <dd>
                <a href={site.modelCardUrl} rel="noreferrer">
                  NCAIR1/N-ATLaS
                </a>
                , based on Llama-3 8B
              </dd>
            </div>
            <div>
              <dt>Languages</dt>
              <dd>English, Hausa, Igbo and Yoruba</dd>
            </div>
            <div>
              <dt>Speech recognition</dt>
              <dd>
                Four official models: Yoruba, Hausa, Igbo and Nigerian accented English
              </dd>
            </div>
            <div>
              <dt>Context length</dt>
              <dd>The model card lists 8,092 tokens</dd>
            </div>
            <div>
              <dt>Access</dt>
              <dd>Gated on Hugging Face. Accept the terms to download the weights.</dd>
            </div>
            <div>
              <dt>Terms</dt>
              <dd>
                Attribution is required, and the terms set a limit of 1,000 active end users before
                a commercial licence is needed.
              </dd>
            </div>
          </dl>
        </div>
      </section>

      <section className="section section--muted" id="languages" aria-labelledby="languages-title">
        <div className="container">
          <h2 id="languages-title" className="section__title">
            Languages
          </h2>
          <p className="section__intro">
            Supported languages follow the official N-ATLAS model cards. Support quality varies
            between languages, so read the evaluation published by the model authors before you
            rely on a language in production.
          </p>
          <div className="table-wrap">
            <table className="table">
              <caption className="visually-hidden">Languages supported by N-ATLAS</caption>
              <thead>
                <tr>
                  <th scope="col">Language</th>
                  <th scope="col">Text generation</th>
                  <th scope="col">Official speech model</th>
                  <th scope="col">In Forge today</th>
                </tr>
              </thead>
              <tbody>
                {languages.map((language) => (
                  <tr key={language.code}>
                    <th scope="row">{language.native}</th>
                    <td>Supported</td>
                    <td>
                      <span className="mono">{language.asrModel}</span>
                    </td>
                    <td>
                      Text through the gateway. Speech <em>in development</em>.
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="section" aria-labelledby="voice-title">
        <div className="container split">
          <div className="split__lead" id="voice">
            <h2 id="voice-title" className="section__title">
              Speech recognition
            </h2>
            <StatusPill tone="neutral">In development</StatusPill>
          </div>
          <p className="split__body">
            NCAIR publishes four speech recognition models. Each is a Whisper Small model that
            expects 16 kHz audio and handles up to 30 seconds per inference. Forge will place them
            behind the same keys, limits and logs as text requests, with audio validated before it
            reaches a model.
          </p>
        </div>
        <div className="container split section__gap" id="mcp">
          <div className="split__lead">
            <h2 className="section__title">Model Context Protocol</h2>
            <StatusPill tone="neutral">In development</StatusPill>
          </div>
          <p className="split__body">
            Agents and developer tools can reach N-ATLAS through the Model Context Protocol. The
            Forge MCP server will expose model information, generation and evaluation as tools,
            with scoped credentials, input validation and logging. It will not expose
            administrative actions such as deleting projects or rotating keys.
          </p>
        </div>
      </section>

      <section
        className="section section--muted"
        id="early-access"
        aria-labelledby="early-access-title"
      >
        <div className="container">
          <h2 id="early-access-title" className="section__title">
            What to expect in early access
          </h2>
          <p className="section__intro">
            Forge is new. This is what is true today, so you can decide how far to rely on it.
          </p>
          <dl className="controls">
            {expectations.map((item) => (
              <div key={item.title} className="controls__item">
                <dt>{item.title}</dt>
                <dd>{item.text}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <section className="section" id="security" aria-labelledby="security-title">
        <div className="container">
          <h2 id="security-title" className="section__title">
            Security
          </h2>
          <p className="section__intro">
            These controls exist in the code today. {securityDisclaimer}
          </p>
          <dl className="controls">
            {securityControls.map((control) => (
              <div key={control.title} className="controls__item">
                <dt>{control.title}</dt>
                <dd>{control.description}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <section className="section section--muted" aria-labelledby="start-building-title">
        <div className="container cta">
          <div>
            <h2 id="start-building-title" className="section__title">
              Start building with N-ATLAS
            </h2>
            <p>
              Create an account, make a project and send your first request. The documentation
              covers every endpoint and error code.
            </p>
          </div>
          <div className="cta__actions">
            {primaryAction}
            <Link to="/docs" className="btn btn--secondary btn--lg">
              Read the documentation
            </Link>
          </div>
        </div>
      </section>

      <section className="section" id="credits" aria-labelledby="credits-title">
        <div className="container split">
          <div className="split__lead">
            <h2 id="credits-title" className="section__title">
              Credits and attribution
            </h2>
          </div>
          <div className="split__body">
            <p>{site.attribution}</p>
            <p>
              NCAIR publishes the N-ATLAS models. N-ATLAS Forge is an independent project by{" "}
              {site.company}. It is not an official product of these organisations and is not
              endorsed by them.
            </p>
            <ul className="link-list">
              <li>
                <a href={site.modelCardUrl} rel="noreferrer">
                  N-ATLaS model card and Terms of Use
                </a>
              </li>
              <li>
                <a href={site.modelOrgUrl} rel="noreferrer">
                  NCAIR on Hugging Face
                </a>
              </li>
              <li>
                <a href={site.repoUrl} rel="noreferrer">
                  Forge source code on GitHub
                </a>
              </li>
            </ul>
          </div>
        </div>
      </section>
    </>
  );
}
