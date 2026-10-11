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
