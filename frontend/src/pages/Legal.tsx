import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { contactHref, site } from "../config";
import { securityControls, securityDisclaimer } from "../content/security";
import { useDocumentTitle } from "../lib/useDocumentTitle";

function LegalPage({ title, children }: { title: string; children: ReactNode }) {
  useDocumentTitle(title);
  return (
    <div className="container legal">
      <h1>{title}</h1>
      {children}
    </div>
  );
}

export function SecurityPage() {
  return (
    <LegalPage title="Security">
      <p className="legal__lead">
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
      <h2>Report a vulnerability</h2>
      <p>
        Please report security issues privately through the repository&apos;s{" "}
        <a href={`${site.repoUrl}/security/policy`} rel="noreferrer">
          security policy
        </a>{" "}
        or through the <a href={contactHref}>contact address</a>. Do not open a public issue for a
        vulnerability.
      </p>
    </LegalPage>
  );
}

export function PrivacyPage() {
  return (
    <LegalPage title="Privacy">
      <p className="legal__lead">
        This page describes what the software stores. It is a plain summary and not a reviewed legal
        policy.
      </p>
      <h2>What Forge stores</h2>
      <ul className="bullets">
        <li>Account data: your email address, an optional name, and an Argon2id password hash.</li>
        <li>
          Session and audit data: a hashed session token, IP address, user agent and timestamps for
          sign-ins and changes to projects and API keys.
        </li>
        <li>
          Request metadata: project, endpoint, status, latency, model, language and token counts for
          each gateway request.
        </li>
        <li>API keys: a public prefix and a keyed hash. The full key is never stored.</li>
      </ul>
      <h2>What Forge does not store</h2>
      <ul className="bullets">
        <li>Prompts and generated responses.</li>
        <li>Plaintext passwords or plaintext API keys.</li>
        <li>Audio content.</li>
      </ul>
      <h2>Cookies and third parties</h2>
      <p>
        Forge sets two cookies, both strictly necessary: one authenticates your session and one
        protects against cross-site requests. There are no analytics, advertising or tracking
        scripts, and fonts are served from the same origin.
      </p>
      <h2>Retention</h2>
      <p>Automatic retention limits are not implemented yet.</p>
      <p>
        Questions about your data: <a href={contactHref}>contact {site.company}</a>.
      </p>
    </LegalPage>
  );
}

export function TermsPage() {
  return (
    <LegalPage title="Terms">
      <p className="legal__lead">
        These terms are a plain-language summary pending legal review. N-ATLAS Forge is built and
        operated by {site.company}, is under active development and is provided as is.
      </p>
      <h2>Your account</h2>
      <p>
        Keep your password and API keys secret. You are responsible for requests made with your keys.
      </p>
      <h2>N-ATLAS terms apply</h2>
      <p>
        Everything you build with the model is subject to the{" "}
        <a href={site.modelCardUrl} rel="noreferrer">
          N-ATLaS Terms of Use
        </a>
        , including its attribution requirement, its prohibited uses and its limit of 1,000 active
        end users before a commercial licence is needed. You are responsible for staying within them.
      </p>
      <h2>Acceptable use</h2>
      <p>
        Do not attempt to disrupt the service, bypass its limits, or access data that is not yours.
        We may suspend keys or accounts that do.
      </p>
      <p>
        See also the <Link to="/privacy">privacy summary</Link> and{" "}
        <Link to="/security">security controls</Link>.
      </p>
    </LegalPage>
  );
}
