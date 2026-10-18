/** Security controls that exist in the code today. Do not list anything that is not implemented. */

export interface Control {
  title: string;
  description: string;
}

export const securityControls: Control[] = [
  {
    title: "API keys",
    description:
      "Generated with 256 bits of entropy, shown once and stored only as keyed HMAC-SHA-256 hashes. Keys can be revoked or rotated at any time.",
  },
  {
    title: "Sessions",
    description:
      "Opaque session tokens in HTTP-only cookies, hashed at rest, with CSRF tokens and Origin checks on every state-changing request.",
  },
  {
    title: "Passwords",
    description:
      "Argon2id hashing, a password policy, and temporary lockout after repeated failed sign-ins.",
  },
  {
    title: "Rate limits",
    description:
      "Redis-backed limits per API key for the gateway and per client address for authentication.",
  },
  {
    title: "Validation",
    description:
      "Strict request schemas, request size limits, and structured errors that never include stack traces or internal paths.",
  },
  {
    title: "Logging",
    description:
      "Structured logs with automatic redaction of keys, tokens and passwords. Request logs never contain prompts or responses. Only the Evaluation Lab stores them, on purpose, so you can inspect failed cases.",
  },
  {
    title: "Access control",
    description:
      "Projects are private to their owner. Other users cannot tell whether a project exists.",
  },
  {
    title: "Auditability",
    description: "Sign-ins, project changes and API key events are written to an audit log.",
  },
  {
    title: "Transport",
    description:
      "Production configuration requires HTTPS and sends HSTS. The inference worker is reached over a private, authenticated connection.",
  },
  {
    title: "Secrets",
    description:
      "Credentials come from environment variables, are never sent to the browser and are never committed to the repository.",
  },
];

export const securityDisclaimer =
  "N-ATLAS Forge has not been independently audited and holds no security certifications.";
