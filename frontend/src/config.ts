/** Site-wide facts. Everything about N-ATLAS here comes from the official model cards. */

export const site = {
  name: "N-ATLAS Forge",
  company: "StackTechnology",
  repoUrl: "https://github.com/PereTheDataAnalyst/forge",
  modelCardUrl: "https://huggingface.co/NCAIR1/N-ATLaS",
  modelOrgUrl: "https://huggingface.co/NCAIR1",
  challengeUrl: "https://ncair.nitda.gov.ng/naic/",
  contactEmail: import.meta.env.VITE_CONTACT_EMAIL as string | undefined,
  /** Attribution text required by the N-ATLaS Terms of Use for public use. */
  attribution:
    "N-ATLaS is an initiative of the Federal Ministry of Communications, Innovation and Digital Economy, and powered by Awarri Technologies.",
} as const;

export const contactHref = site.contactEmail
  ? `mailto:${site.contactEmail}`
  : `${site.repoUrl}/issues`;

export type Availability = "available" | "in_development";

export interface Layer {
  name: string;
  summary: string;
  status: Availability;
}

/** Update `status` only when a capability can actually be used in the product. */
export const layers: Layer[] = [
  {
    name: "API gateway",
    summary:
      "One versioned API in front of N-ATLAS with authentication, rate limits, input validation, request IDs and structured errors.",
    status: "available",
  },
  {
    name: "Projects and API keys",
    summary:
      "Project-scoped keys that are shown once, stored only as keyed hashes, and can be revoked or rotated.",
    status: "available",
  },
  {
    name: "Observability",
    summary:
      "Status, latency, model, language and token counts recorded for every request and summarised on the dashboard.",
    status: "available",
  },
  {
    name: "Documentation",
    summary: "Reference for every endpoint that exists, with the exact request and response shapes.",
    status: "available",
  },
  {
    name: "Playground",
    summary: "An interactive workspace for prompts, parameters, conversation history and raw requests.",
    status: "available",
  },
  {
    name: "SDKs",
    summary: "Typed clients for JavaScript and TypeScript, and for Python, that expose request IDs.",
    status: "in_development",
  },
  {
    name: "Evaluation",
    summary: "Test cases and runs with deterministic checks, latency and failure rates per language.",
    status: "in_development",
  },
  {
    name: "Voice",
    summary: "Audio upload and recording in front of the official N-ATLAS speech recognition models.",
    status: "in_development",
  },
  {
    name: "MCP",
    summary: "A Model Context Protocol server with scoped credentials and no administrative tools.",
    status: "in_development",
  },
];

export interface LanguageInfo {
  code: "en" | "ha" | "ig" | "yo";
  name: string;
  native: string;
  asrModel: string;
  asrNote: string;
}

export const languages: LanguageInfo[] = [
  {
    code: "en",
    name: "English",
    native: "English",
    asrModel: "NCAIR1/NigerianAccentedEnglish",
    asrNote: "Nigerian accented English",
  },
  { code: "ha", name: "Hausa", native: "Hausa", asrModel: "NCAIR1/Hausa-ASR", asrNote: "Hausa" },
  { code: "ig", name: "Igbo", native: "Igbo", asrModel: "NCAIR1/Igbo-ASR", asrNote: "Igbo" },
  { code: "yo", name: "Yoruba", native: "Yorùbá", asrModel: "NCAIR1/Yoruba-ASR", asrNote: "Yoruba" },
];

export const DEFAULT_MODEL_ID = "NCAIR1/N-ATLaS";

export interface ScopeOption {
  id: string;
  label: string;
  description: string;
}

/** Scopes a developer can select when creating a key. Keep in sync with backend/app/core/scopes.py. */
export const selectableScopes: ScopeOption[] = [
  { id: "models.read", label: "models.read", description: "List the models available through the gateway." },
  { id: "chat.create", label: "chat.create", description: "Generate replies with POST /api/v1/chat." },
];
