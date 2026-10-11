import type {
  ApiKey,
  CreatedApiKey,
  DashboardSummary,
  Project,
  Readiness,
  RotatedApiKey,
  User,
} from "./types";

const API_PREFIX = "/api/v1";
const CSRF_COOKIE = "forge_csrf";
const SESSION_ERRORS = new Set(["not_authenticated", "session_expired"]);

export interface FieldIssue {
  field: string;
  message: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly details: FieldIssue[];
  readonly retryAfterSeconds: number | null;

  constructor(
    status: number,
    code: string,
    message: string,
    requestId: string | null = null,
    details: FieldIssue[] = [],
    retryAfterSeconds: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.details = details;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

let unauthorizedHandler: (() => void) | null = null;

/** Called when an authenticated request fails because the session ended. */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function toApiError(response: Response, body: unknown): ApiError {
  const requestId = response.headers.get("x-request-id");
  const retryHeader = response.headers.get("retry-after");
  const retryAfter = retryHeader && /^\d+$/.test(retryHeader) ? Number(retryHeader) : null;
  if (isRecord(body) && isRecord(body.error)) {
    const error = body.error;
    const details: FieldIssue[] = Array.isArray(error.details)
      ? error.details.flatMap((item: unknown) =>
          isRecord(item) && typeof item.field === "string" && typeof item.message === "string"
            ? [{ field: item.field, message: item.message }]
            : [],
        )
      : [];
    return new ApiError(
      response.status,
      typeof error.code === "string" ? error.code : "http_error",
      typeof error.message === "string" ? error.message : "The request failed.",
      typeof error.request_id === "string" ? error.request_id : requestId,
      details,
      retryAfter,
    );
  }
  return new ApiError(response.status, "http_error", "The request failed.", requestId, [], retryAfter);
}

async function request<T>(
  method: "GET" | "POST" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }

  let response: Response;
  try {
    response = await fetch(`${API_PREFIX}${path}`, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(
      0,
      "network_error",
      "Could not reach the Forge API. Check that the backend is running and try again.",
    );
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }

  if (!response.ok) {
    const error = toApiError(response, parsed);
    if (error.status === 401 && SESSION_ERRORS.has(error.code)) unauthorizedHandler?.();
    throw error;
  }
  return parsed as T;
}

export const api = {
  auth: {
    me: () => request<User>("GET", "/auth/me"),
    login: (email: string, password: string) =>
      request<User>("POST", "/auth/login", { email, password }),
    register: (email: string, password: string, displayName?: string) =>
      request<User>("POST", "/auth/register", {
        email,
        password,
        display_name: displayName || undefined,
      }),
    logout: () => request<void>("POST", "/auth/logout"),
  },
  projects: {
    list: () => request<{ data: Project[] }>("GET", "/projects").then((r) => r.data),
    get: (id: string) => request<Project>("GET", `/projects/${id}`),
    create: (name: string, description?: string) =>
      request<Project>("POST", "/projects", { name, description: description || undefined }),
    update: (id: string, changes: { name?: string; description?: string | null }) =>
      request<Project>("PATCH", `/projects/${id}`, changes),
    archive: (id: string) => request<void>("DELETE", `/projects/${id}`),
  },
  keys: {
    list: (projectId: string) =>
      request<{ data: ApiKey[] }>("GET", `/projects/${projectId}/api-keys`).then((r) => r.data),
    create: (
      projectId: string,
      input: { name: string; scopes?: string[]; expires_in_days?: number },
    ) => request<CreatedApiKey>("POST", `/projects/${projectId}/api-keys`, input),
    revoke: (projectId: string, keyId: string) =>
      request<ApiKey>("POST", `/projects/${projectId}/api-keys/${keyId}/revoke`),
    rotate: (projectId: string, keyId: string, gracePeriodSeconds = 0) =>
      request<RotatedApiKey>("POST", `/projects/${projectId}/api-keys/${keyId}/rotate`, {
        grace_period_seconds: gracePeriodSeconds,
      }),
  },
  dashboard: {
    summary: (days = 30) => request<DashboardSummary>("GET", `/dashboard/summary?days=${days}`),
  },
  health: {
    ready: () => request<Readiness>("GET", "/health/ready"),
  },
};
