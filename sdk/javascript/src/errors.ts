/** Errors thrown by the client. Every one that comes from a server response carries its request ID. */

export interface ForgeErrorInit {
  status?: number;
  code?: string;
  requestId?: string;
  details?: unknown;
  retryAfter?: number;
  cause?: unknown;
}

/**
 * Base class for every error this package throws on purpose.
 *
 * `message` is safe to show. `code` is the API's stable error code, for example `rate_limited`.
 * `requestId` is the ID in the `X-Request-ID` header and in the Request logs page. `retryAfter`
 * is in seconds when the server said how long to wait.
 */
export class ForgeError extends Error {
  readonly status: number | undefined;
  readonly code: string | undefined;
  readonly requestId: string | undefined;
  readonly details: unknown;
  readonly retryAfter: number | undefined;

  constructor(message: string, init: ForgeErrorInit = {}) {
    super(message, init.cause !== undefined ? { cause: init.cause } : undefined);
    this.name = new.target.name;
    this.status = init.status;
    this.code = init.code;
    this.requestId = init.requestId;
    this.details = init.details;
    this.retryAfter = init.retryAfter;
  }

  override toString(): string {
    const parts: string[] = [];
    if (this.code) parts.push(`code=${this.code}`);
    if (this.status !== undefined) parts.push(`status=${this.status}`);
    if (this.requestId) parts.push(`requestId=${this.requestId}`);
    return parts.length > 0 ? `${this.name}: ${this.message} (${parts.join(", ")})` : `${this.name}: ${this.message}`;
  }
}

/** The server could not be reached (DNS, refused connection, TLS, reset). */
export class ForgeConnectionError extends ForgeError {}
/** The server did not answer within the timeout. */
export class ForgeTimeoutError extends ForgeConnectionError {}
/** The caller's AbortSignal ended the request. */
export class ForgeAbortError extends ForgeError {}
/** The server answered with an error status. */
export class ForgeStatusError extends ForgeError {}
/** 400, 413, 415 or 422: the request itself was refused. Fix it before trying again. */
export class ForgeInvalidRequestError extends ForgeStatusError {}
/** 401: the API key is missing, wrong, revoked or expired. */
export class ForgeAuthenticationError extends ForgeStatusError {}
/** 403: the key lacks the scope, or the project is archived. */
export class ForgePermissionError extends ForgeStatusError {}
/** 404: for example an unknown model id. */
export class ForgeNotFoundError extends ForgeStatusError {}
/** 429: slow down. `retryAfter` says how long to wait when the server told us. */
export class ForgeRateLimitError extends ForgeStatusError {}
/** 5xx: the platform or the model host failed. Often worth retrying later. */
export class ForgeServerError extends ForgeStatusError {}

/** Seconds from a Retry-After header that holds a number. This API never sends a date. */
export function parseRetryAfter(value: string | null | undefined): number | undefined {
  if (value === null || value === undefined) return undefined;
  const seconds = Number(value.trim());
  return Number.isFinite(seconds) && seconds >= 0 && seconds < 86_400 ? seconds : undefined;
}

/** Build the right error from an error response. Never includes request content. */
export function errorFromResponse(status: number, body: unknown, headers: Headers): ForgeStatusError {
  const info =
    typeof body === "object" && body !== null && typeof (body as { error?: unknown }).error === "object"
      ? ((body as { error: Record<string, unknown> }).error ?? {})
      : {};
  const message = typeof info.message === "string" && info.message ? info.message : `The server answered with status ${status}.`;
  const init: ForgeErrorInit = { status };
  if (typeof info.code === "string" && info.code) init.code = info.code;
  const requestId = typeof info.request_id === "string" && info.request_id ? info.request_id : headers.get("x-request-id");
  if (requestId) init.requestId = requestId;
  if (info.details !== undefined) init.details = info.details;
  const retryAfter = parseRetryAfter(headers.get("retry-after"));
  if (retryAfter !== undefined) init.retryAfter = retryAfter;
  if (status === 401) return new ForgeAuthenticationError(message, init);
  if (status === 403) return new ForgePermissionError(message, init);
  if (status === 404) return new ForgeNotFoundError(message, init);
  if (status === 429) return new ForgeRateLimitError(message, init);
  if (status >= 500) return new ForgeServerError(message, init);
  return new ForgeInvalidRequestError(message, init);
}
