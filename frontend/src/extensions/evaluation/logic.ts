/** Pure helpers for the Evaluation Lab. No React, no network, so they can be tested alone. */

import type { Tone } from "../../components/StatusPill";
import type {
  CaseResult,
  CaseStatus,
  CheckParam,
  CheckResult,
  RunState,
  StatusCounts,
} from "../../lib/types";

export const STATUS_ORDER: CaseStatus[] = ["pass", "fail", "review", "error", "skipped"];

export const STATUS_LABEL: Record<CaseStatus, string> = {
  pass: "Passed",
  fail: "Failed",
  review: "Needs review",
  error: "Error",
  skipped: "Skipped",
};

export const STATUS_TONE: Record<CaseStatus, Tone> = {
  pass: "success",
  fail: "danger",
  review: "warning",
  error: "danger",
  skipped: "neutral",
};

/** What each status means, in the engine's own terms. Shown as a tooltip and in the help. */
export const STATUS_HINT: Record<CaseStatus, string> = {
  pass: "Every check held.",
  fail: "At least one check did not hold.",
  review: "A check could not decide. Read the output yourself.",
  error: "The model call failed, so this case was not evaluated.",
  skipped: "The run was cancelled before this case started.",
};

export const RUN_STATE: Record<RunState, { tone: Tone; label: string }> = {
  running: { tone: "info", label: "Running" },
  completed: { tone: "success", label: "Completed" },
  cancelled: { tone: "neutral", label: "Cancelled" },
  failed: { tone: "danger", label: "Failed" },
};

export function formatRate(rate: number | null | undefined): string {
  if (rate === null || rate === undefined) return "Not measured";
  return `${Math.round(rate * 100)}%`;
}

export function progressPercent(done: number, total: number): number {
  if (total <= 0) return 0;
  return Math.min(100, Math.max(0, Math.round((done / total) * 100)));
}

export function shortRunId(runId: string): string {
  return runId.slice(0, 8);
}

export function checksHeld(checks: readonly CheckResult[]): { held: number; total: number } {
  return { held: checks.filter((check) => check.passed === true).length, total: checks.length };
}

export function needsAttention(status: CaseStatus): boolean {
  return status === "fail" || status === "review" || status === "error";
}

export function attentionCount(counts: StatusCounts): number {
  return counts.fail + counts.review + counts.error;
}

export type ResultFilter = "all" | "attention";

export function filterResults(results: readonly CaseResult[], filter: ResultFilter): CaseResult[] {
  return filter === "all" ? [...results] : results.filter((result) => needsAttention(result.status));
}

const ERROR_TEXT: Record<string, string> = {
  provider_unavailable: "The model host could not be reached.",
  provider_timeout: "The model took too long to answer.",
  provider_busy: "The model host was busy with other work.",
  provider_loading: "The model was still loading.",
  provider_not_configured: "No model host is connected.",
  rate_limited: "The request rate limit was reached.",
  model_not_found: "The chosen model is not available.",
  invalid_parameters: "The request settings were rejected.",
  internal_error: "An internal error stopped this case.",
};

/** A plain sentence for an error code, or the code itself when it is not a known one. */
export function describeErrorCode(code: string | null | undefined): string {
  if (!code) return "The case failed for an unknown reason.";
  return ERROR_TEXT[code] ?? `The case failed with the code ${code}.`;
}

/** One line per parameter, with the required ones marked. */
export function describeParams(params: Record<string, CheckParam>): string {
  const names = Object.entries(params).map(([name, spec]) =>
    spec.required ? `${name} (required)` : name,
  );
  return names.length > 0 ? names.join(", ") : "No parameters";
}

// The server accepts exactly these characters, because the label ends up in exports.
const PROMPT_VERSION_PATTERN = /^[A-Za-z0-9 _.:/@+#-]{1,64}$/;

/** An empty label is fine: it is optional. */
export function isValidPromptVersion(text: string): boolean {
  const trimmed = text.trim();
  return trimmed === "" || PROMPT_VERSION_PATTERN.test(trimmed);
}

/** A small valid dataset the developer can start from. The tests validate it with the engine. */
export const EXAMPLE_DATASET = {
  name: "Greetings and arithmetic",
  description: "Two cases with checks written as rules, not as a score from another model.",
  cases: [
    {
      id: "hello-en",
      name: "English greeting",
      category: "general",
      language: "en",
      prompt: "Say hello in one short sentence.",
      checks: [{ type: "not_empty" }, { type: "length_between", max_chars: 200 }],
    },
    {
      id: "arithmetic",
      name: "Simple arithmetic",
      category: "factual",
      language: "en",
      prompt: "What is 12 plus 30? Answer with the number only.",
      checks: [{ type: "contains_all", values: ["42"] }],
    },
  ],
};

export const EXAMPLE_DATASET_TEXT = JSON.stringify(EXAMPLE_DATASET, null, 2);

export type ParsedDataset = { value: unknown } | { error: string };

/** Reads pasted text. Structure and values are checked by the server, with a message per field. */
export function parseDatasetText(text: string): ParsedDataset {
  const trimmed = text.trim();
  if (!trimmed) return { error: "Paste a dataset first." };
  let value: unknown;
  try {
    value = JSON.parse(trimmed);
  } catch (error) {
    const reason = error instanceof Error ? ` ${error.message}` : "";
    return { error: `That is not valid JSON.${reason}` };
  }
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return { error: "A dataset is a JSON object with a name and a list of cases." };
  }
  return { value };
}
