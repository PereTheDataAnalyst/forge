/** Shapes returned by the Forge API. They mirror backend/app/schemas exactly. */

export interface User {
  id: string;
  email: string;
  display_name: string | null;
  created_at: string;
}

export interface Project {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
  active_key_count: number;
}

export type KeyStatus = "active" | "revoked" | "expired";

export interface ApiKey {
  id: string;
  project_id: string;
  name: string;
  prefix: string;
  scopes: string[];
  status: KeyStatus;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
}

export interface CreatedApiKey extends ApiKey {
  /** The full key. Returned once and never retrievable again. */
  key: string;
}

export interface RotatedApiKey {
  new_key: CreatedApiKey;
  previous: ApiKey;
}

export type ProviderState = "ok" | "not_configured" | "unreachable" | "loading" | "error";

export interface ProviderStatus {
  status: ProviderState;
  model_id: string | null;
  detail: string | null;
}

export interface DashboardTotals {
  projects: number;
  active_api_keys: number;
  requests: number;
  successful: number;
  failed: number;
  average_latency_ms: number | null;
}

export interface RecentRequest {
  id: string;
  request_id: string;
  created_at: string;
  project_id: string;
  project_name: string;
  endpoint: string;
  method: string;
  status_code: number;
  outcome: string;
  error_code: string | null;
  latency_ms: number | null;
  model: string | null;
  language: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
}

export interface RecentApiKey {
  id: string;
  project_id: string;
  project_name: string;
  name: string;
  prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
}

export interface RecentEvaluationRun {
  id: string;
  /** The project that owns the run, so the dashboard can link to its result page. */
  project_id?: string;
  status: string;
  evaluation_name?: string | null;
  model?: string | null;
  created_at?: string | null;
}

export interface DashboardSummary {
  window_days: number;
  totals: DashboardTotals;
  provider: ProviderStatus;
  recent_requests: RecentRequest[];
  recent_api_keys: RecentApiKey[];
  recent_evaluation_runs: RecentEvaluationRun[];
}

export interface ReadinessCheck {
  status: string;
  model_id?: string | null;
  detail?: string | null;
}

export interface Readiness {
  status: "ok" | "degraded" | "unavailable";
  checks: { database: ReadinessCheck; redis: ReadinessCheck; provider: ReadinessCheck };
}

export type LanguageCode = "en" | "ha" | "ig" | "yo";
export type ChatRole = "system" | "user" | "assistant";

export interface ChatMessage {
  role: ChatRole;
  content: string;
}

/** Body of POST /api/v1/chat and POST /api/v1/projects/{id}/playground/chat. */
export interface ChatRequestBody {
  model?: string;
  messages: ChatMessage[];
  max_tokens?: number;
  temperature?: number;
  top_p?: number;
  repetition_penalty?: number;
  language?: LanguageCode;
}

export interface ChatUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface ChatResponse {
  request_id: string;
  model: string;
  message: { role: "assistant"; content: string };
  finish_reason: "stop" | "length";
  usage: ChatUsage;
  latency_ms: number;
}

export type ModelStatus = "available" | "unavailable" | "not_configured" | "loading";

export interface ModelInfo {
  id: string;
  kind: "llm" | "asr";
  display_name: string;
  architecture: string | null;
  parameters: string | null;
  languages: string[];
  context_length_tokens: number | null;
  license: string;
  source_url: string;
  attribution: string | null;
  status: ModelStatus;
}

// -- Evaluation Lab ---------------------------------------------------------------------------

export type CaseStatus = "pass" | "fail" | "review" | "error" | "skipped";

export interface StatusCounts {
  pass: number;
  fail: number;
  review: number;
  error: number;
  skipped: number;
}

export interface GroupSummary {
  total: number;
  counts: StatusCounts;
  completed: number;
  pass_rate: number | null;
  check_failure_rate: number | null;
  review_rate: number | null;
  error_rate: number | null;
}

export interface LatencySummary {
  count: number;
  min: number | null;
  mean: number | null;
  p50: number | null;
  p95: number | null;
  max: number | null;
}

export interface RunSummary extends GroupSummary {
  by_category: Record<string, GroupSummary>;
  by_language: Record<string, GroupSummary>;
  attempts: {
    total: number;
    failed: number;
    failure_rate: number | null;
    errors_by_code: Record<string, number>;
  };
  latency_ms: LatencySummary | null;
  checks: Record<string, { pass: number; fail: number; review: number }>;
  consistency: Record<string, unknown> | null;
}

export interface CheckResult {
  type: string;
  passed: boolean | null;
  detail: string;
  evidence: Record<string, unknown>;
}

export interface Attempt {
  index: number;
  request_id: string;
  ok: boolean;
  latency_ms: number | null;
  provider_latency_ms: number | null;
  output: string | null;
  output_truncated: boolean;
  model: string | null;
  usage: ChatUsage | null;
  finish_reason: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface CaseResult {
  case_id: string;
  name: string;
  category: string;
  language: string | null;
  status: CaseStatus;
  attempts: Attempt[];
  checks: CheckResult[];
  error_code: string | null;
  error_message: string | null;
  partial_error_count: number;
}

export type RunState = "running" | "completed" | "cancelled" | "failed";

export interface RunDocument {
  run_id: string;
  dataset_id: string;
  dataset_name: string;
  dataset_version: string;
  model: string | null;
  prompt_version: string | null;
  project_id: string | null;
  status: "completed" | "cancelled";
  started_at: string;
  finished_at: string;
  config: Record<string, unknown>;
  results: CaseResult[];
  summary: RunSummary;
  methodology: string[];
}

export interface RunHandle {
  run_id: string;
  dataset_id: string;
  dataset_name: string;
  state: RunState;
  cases_total: number;
  cases_done: number;
  created_at: string;
  finished_at: string | null;
  error_code: string | null;
  error_message: string | null;
  /** null while the finished run is still being saved. */
  persisted: boolean | null;
}

export interface RunStatus {
  state: RunState;
  handle: RunHandle | null;
  run: RunDocument | null;
}

export interface RunListItem {
  run_id: string;
  dataset_id: string | null;
  dataset_name: string | null;
  state: RunState;
  model: string | null;
  prompt_version: string | null;
  started_at: string | null;
  finished_at: string | null;
  cases_total: number | null;
  cases_done: number | null;
  pass_rate: number | null;
  counts: StatusCounts | null;
}

export interface EvaluationSummary {
  id: string;
  name: string;
  description: string | null;
  case_count: number;
  created_at: string;
}

export interface EvaluationCaseView {
  id: string;
  position: number;
  key: string | null;
  name: string | null;
  category: string;
  language: string | null;
  prompt: string;
  system: string | null;
  checks: ({ type: string } & Record<string, unknown>)[];
  repeat: number;
  max_tokens: number | null;
  temperature: number | null;
  tags: string[];
}

export interface EvaluationDetail extends EvaluationSummary {
  cases: EvaluationCaseView[];
}

export interface StarterDataset {
  id: string;
  name: string;
  version: string;
  description: string;
  case_count: number;
  languages: string[];
  categories: string[];
}

export interface CheckParam {
  type?: string;
  required?: boolean;
  default?: unknown;
  enum?: string[];
}

export interface CheckType {
  type: string;
  description: string;
  params: Record<string, CheckParam>;
}
