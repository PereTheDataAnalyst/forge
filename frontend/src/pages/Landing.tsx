import { Link } from "react-router-dom";
import { StatusPill } from "../components/StatusPill";
import { languages, layers, site } from "../config";
import { securityControls, securityDisclaimer } from "../content/security";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { RequestConsole } from "./landing/RequestConsole";

const flow = [
  {
    title: "Discover",
    text: "Read what N-ATLAS supports and what Forge exposes in the documentation.",
  },
  {
    title: "Create a project",
    text: "Group keys, usage and logs for each application you build.",
  },
  {
    title: "Generate a key",
    text: "Keys are scoped to a project, shown once, and stored only as keyed hashes.",
  },
  {
    title: "Call N-ATLAS",
    text: "Send chat requests to one versioned endpoint and read the request ID on every response.",
  },
  {
    title: "Evaluate",
    text: "Run test cases against the model and compare outputs. This layer is in development.",
  },
  {
    title: "Monitor",
    text: "Track successes, failures and latency from real traffic on the dashboard.",
  },
  {
    title: "Deploy",
    text: "Run the gateway and the inference worker on infrastructure you control. Deployment notes are in the documentation.",
  },
];

export function LandingPage() {
  useDocumentTitle();

  return (
    <>
      <section className="hero">
        <div className="container hero__grid">
          <div className="hero__copy">
            <h1 className="hero__title">Build with N-ATLAS.</h1>
            <p className="hero__lead">
              Forge is the developer infrastructure around N-ATLAS. It gives you API keys, a
              gateway, request logs and usage, with SDKs, a playground, evaluation and voice
              tooling built on the same foundation.
            </p>
            <div className="hero__actions">
              <Link to="/register" className="btn btn--primary btn--lg">
                Start building
              </Link>
              <a href="#console" className="btn btn--secondary btn--lg">
                Explore the playground
              </a>
            </div>
            <p className="hero__meta">
              Built around open weights published by NCAIR. Text in English, Hausa, Igbo and
              Yoruba.
            </p>
          </div>
          <RequestConsole />
        </div>
      </section>

      <section className="section" id="n-atlas" aria-labelledby="n-atlas-title">
        <div className="container split">
          <div className="split__lead">
            <h2 id="n-atlas-title" className="section__title">
              What N-ATLAS is
            </h2>
            <p>
              N-ATLAS, the Nigerian Atlas for Languages and AI at Scale, is Nigeria&apos;s open
              multilingual language model initiative. The text model is fine-tuned from Llama-3 8B
              and works in English, Hausa, Igbo and Yoruba.
            </p>
            <p>
              The National Centre for Artificial Intelligence and Robotics (NCAIR) publishes the
              weights on Hugging Face under the N-ATLaS Terms of Use. A model of this size needs
              real infrastructure to run, and every application then needs the same things around
              it: keys, limits, logs and a stable API. That is the layer Forge provides.
            </p>
          </div>
          <dl className="facts">
            <div>
              <dt>Text model</dt>
              <dd>
                <a href={site.modelCardUrl} rel="noreferrer">
                  NCAIR1/N-ATLaS
                </a>
                , 8 billion parameters
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

      <section className="section section--muted" id="platform" aria-labelledby="platform-title">
        <div className="container">
          <h2 id="platform-title" className="section__title">
            What Forge adds
          </h2>
          <p className="section__intro">
            Every layer is listed with its current status, so you know what you can use today.
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

          <div className="table-wrap layers">
            <table className="table">
              <caption className="visually-hidden">Forge layers and their status</caption>
              <thead>
                <tr>
                  <th scope="col">Layer</th>
                  <th scope="col">What it does</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {layers.map((layer) => (
                  <tr key={layer.name}>
                    <th scope="row" className="layers__name">
                      {layer.name}
                    </th>
                    <td>{layer.summary}</td>
                    <td>
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
      </section>

      <section className="section" id="flow" aria-labelledby="flow-title">
        <div className="container split">
          <div className="split__lead">
            <h2 id="flow-title" className="section__title">
              From account to production
            </h2>
            <p>
              The same path applies whether you are testing a prompt or shipping an application.
            </p>
          </div>
          <ol className="flow">
            {flow.map((step) => (
              <li key={step.title}>
                <h3>{step.title}</h3>
                <p>{step.text}</p>
              </li>
            ))}
          </ol>
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
            NCAIR publishes four speech recognition models. The Yoruba and Nigerian accented
            English models are fine-tunes of Whisper Small that expect 16 kHz audio and handle up
            to 30 seconds per inference. Forge will place them behind the same keys, limits and
            logs as text requests, with audio validated before it reaches a model.
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

      <section className="section section--muted" id="security" aria-labelledby="security-title">
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

      <section className="section" aria-labelledby="docs-title">
        <div className="container cta">
          <div>
            <h2 id="docs-title" className="section__title">
              Read the documentation
            </h2>
            <p>
              Quick start, authentication, every endpoint that exists, error codes, and how to
              connect the N-ATLAS inference worker.
            </p>
          </div>
          <Link to="/docs" className="btn btn--primary btn--lg">
            Open the documentation
          </Link>
        </div>
      </section>

      <section className="section section--muted" id="credits" aria-labelledby="credits-title">
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
                <a href={site.challengeUrl} rel="noreferrer">
                  National AI Innovation Challenge 2026
                </a>
              </li>
            </ul>
          </div>
        </div>
      </section>
    </>
  );
}
