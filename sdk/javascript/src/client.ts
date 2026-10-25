import {
  ForgeAbortError,
  ForgeConnectionError,
  ForgeError,
  ForgeTimeoutError,
  errorFromResponse,
} from "./errors.js";
import type {
  ChatCompletion,
  ChatParams,
  Model,
  ModelList,
  RateLimit,
  RequestOptions,
  TranscribeParams,
  Transcription,
} from "./types.js";

export const VERSION = "0.1.0";

const DEFAULT_TIMEOUT_MS = 60_000;
const DEFAULT_MAX_RETRIES = 2;
const MAX_RETRY_DELAY_SECONDS = 30;
const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
const MAX_AUDIO_BYTES = 25 * 1024 * 1024;
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);
const REQUEST_ID = /^[A-Za-z0-9._-]{8,64}$/;
const BAD_KEY_CHARS = /[\u0000-\u0020\u007f]/;
// A 503 with one of these codes will not change by waiting, so it is not retried.
const NO_RETRY_CODES = new Set(["provider_not_configured", "provider_misconfigured", "mcp_disabled"]);
// Node reports these when the connection could not be made, so nothing was sent.
const CONNECT_STAGE_CODES = new Set(["ECONNREFUSED", "ENOTFOUND", "EAI_AGAIN", "ENETUNREACH", "EHOSTUNREACH"]);

export interface ForgeOptions {
  /** A project API key, `nfk_...`. Keep it on a server. Never put it in browser code. */
  apiKey: string;
  /** The address of your Forge deployment, for example `https://forge.example.com`. */
  baseUrl: string;
  /** Per attempt, in milliseconds. Default 60000. */
  timeoutMs?: number;
  /** Retries after the first try, for failures that are safe to repeat. Default 2. */
  maxRetries?: number;
  /** Allow `http://` to a host that is not loopback. Only for a private network you control. */
  allowInsecureHttp?: boolean;
  /** A `fetch` function, for a proxy agent or a test double. Defaults to the global `fetch`. */
  fetch?: typeof fetch;
  /** Replaces the wait between retries. For tests. */
  sleep?: (milliseconds: number) => Promise<void>;
}

interface RequestInit2 {
  query?: Record<string, string>;
  jsonBody?: Record<string, unknown>;
  rawBody?: Blob | Uint8Array;
  contentType?: string;
}

interface Answer {
  body: Record<string, unknown>;
  headers: Headers;
}

class ShapeError extends Error {}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function needString(source: Record<string, unknown>, key: string): string {
  const value = source[key];
  if (typeof value !== "string") throw new ShapeError(key);
  return value;
}

function needNumber(source: Record<string, unknown>, key: string): number {
  const value = source[key];
  if (typeof value !== "number" || !Number.isFinite(value)) throw new ShapeError(key);
  return value;
}

function needRecord(source: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = source[key];
  if (!isRecord(value)) throw new ShapeError(key);
  return value;
}

function optString(source: Record<string, unknown>, key: string): string | null {
  const value = source[key];
  return typeof value === "string" ? value : null;
}

function optNumber(source: Record<string, unknown>, key: string): number | null {
  const value = source[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function normaliseBaseUrl(value: string, allowInsecureHttp: boolean): string {
  let url: URL;
  try {
    url = new URL(value.trim());
  } catch {
    throw new TypeError("baseUrl must be a full address such as https://forge.example.com");
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") {
    throw new TypeError("baseUrl must start with https:// (or http:// for a local server).");
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new TypeError("baseUrl must not contain credentials, a query or a fragment.");
  }
  if (url.protocol === "http:" && !LOOPBACK_HOSTS.has(url.hostname) && !allowInsecureHttp) {
    throw new TypeError(
      "Refusing to send an API key over plain http to a remote host. Use https://, " +
        "or set allowInsecureHttp for a private network you control.",
    );
  }
  let path = url.pathname.replace(/\/+$/, "");
  if (!path.endsWith("/api/v1")) path += "/api/v1";
  return `${url.protocol}//${url.host}${path}`;
}

function rateLimitFrom(headers: Headers): RateLimit {
  const read = (name: string): number | undefined => {
    const raw = headers.get(name);
    if (raw === null) return undefined;
    const value = Number.parseInt(raw, 10);
    return Number.isNaN(value) ? undefined : value;
  };
  return {
    limit: read("x-ratelimit-limit"),
    remaining: read("x-ratelimit-remaining"),
    resetSeconds: read("x-ratelimit-reset"),
  };
}

function badResponse(status: number, headers: Headers, what: string): ForgeError {
  const init: { status: number; code: string; requestId?: string } = { status, code: "invalid_response" };
  const requestId = headers.get("x-request-id");
  if (requestId) init.requestId = requestId;
  return new ForgeError(`The server sent a response this client could not read (${what}).`, init);
}

function isConnectStageError(error: unknown): boolean {
  const cause = (error as { cause?: { code?: unknown; errors?: Array<{ code?: unknown }> } } | null)?.cause;
  const codes = [cause?.code, ...(cause?.errors ?? []).map((item) => item.code)];
  return codes.some((code) => typeof code === "string" && CONNECT_STAGE_CODES.has(code));
}

async function readBounded(response: Response, limit: number): Promise<string | null> {
  if (!response.body) return "";
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > limit) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const joined = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    joined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder().decode(joined);
}

function tryParse(text: string): unknown {
  if (!text) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return undefined;
  }
}

function isRetryable(method: string, error: ForgeError): boolean {
  if (error.code !== undefined && NO_RETRY_CODES.has(error.code)) return false;
  if (error.status === 429 || error.status === 503) return true; // refused before any work was done
  return method === "GET" && (error.status === 502 || error.status === 504);
}

/** Milliseconds to wait before retry number `attempt + 1`, or undefined to stop retrying. */
function backoff(attempt: number, retryAfterSeconds: number | undefined): number | undefined {
  if (retryAfterSeconds !== undefined) {
    return retryAfterSeconds <= MAX_RETRY_DELAY_SECONDS ? retryAfterSeconds * 1000 : undefined;
  }
  return Math.min(500 * 2 ** attempt, 8000) * (0.5 + Math.random() * 0.5);
}

class Core {
  readonly #apiKey: string;
  readonly baseUrl: string;
  readonly #timeoutMs: number;
  readonly #maxRetries: number;
  readonly #fetch: typeof fetch;
  readonly #sleep: (milliseconds: number) => Promise<void>;
  readonly #userAgent: string;

  constructor(options: ForgeOptions) {
    const { apiKey, baseUrl } = options;
    if (typeof apiKey !== "string" || apiKey === "" || BAD_KEY_CHARS.test(apiKey)) {
      throw new TypeError("apiKey is required. A key has no spaces or line breaks.");
    }
    if (typeof baseUrl !== "string" || baseUrl === "") {
      throw new TypeError("baseUrl is required, for example https://forge.example.com");
    }
    const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const maxRetries = options.maxRetries ?? DEFAULT_MAX_RETRIES;
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 600_000) {
      throw new TypeError("timeoutMs must be between 0 and 600000.");
    }
    if (!Number.isInteger(maxRetries) || maxRetries < 0 || maxRetries > 10) {
      throw new TypeError("maxRetries must be a whole number from 0 to 10.");
    }
    const fetchFunction = options.fetch ?? (globalThis as { fetch?: typeof fetch }).fetch;
    if (typeof fetchFunction !== "function") {
      throw new TypeError("No fetch function is available. Use Node 18 or newer, or pass options.fetch.");
    }
    this.#apiKey = apiKey;
    this.baseUrl = normaliseBaseUrl(baseUrl, options.allowInsecureHttp === true);
    this.#timeoutMs = timeoutMs;
    this.#maxRetries = maxRetries;
    this.#fetch = fetchFunction;
    this.#sleep = options.sleep ?? ((milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)));
    const runtime = (globalThis as { process?: { version?: string } }).process?.version;
    this.#userAgent = `natlas-forge-js/${VERSION}${runtime ? ` node/${runtime}` : ""}`;
  }

  get keyHint(): string {
    return this.#apiKey.length > 8 ? `${this.#apiKey.slice(0, 8)}...` : "...";
  }

  async request(
    method: "GET" | "POST",
    path: string,
    init: RequestInit2 = {},
    options: RequestOptions = {},
  ): Promise<Answer> {
    let url = this.baseUrl + path;
    if (init.query) url += `?${new URLSearchParams(init.query).toString()}`;
    const headers = new Headers({
      Authorization: `Bearer ${this.#apiKey}`,
      Accept: "application/json",
      "User-Agent": this.#userAgent,
    });
    let body: Blob | Uint8Array | string | undefined;
    if (init.jsonBody !== undefined) {
      body = JSON.stringify(init.jsonBody);
      headers.set("Content-Type", "application/json; charset=utf-8");
    } else if (init.rawBody !== undefined) {
      body = init.rawBody;
      headers.set("Content-Type", init.contentType ?? "application/octet-stream");
    }
    if (options.requestId !== undefined) {
      if (!REQUEST_ID.test(options.requestId)) {
        throw new TypeError("requestId must be 8 to 64 letters, digits, dots, dashes or underscores.");
      }
      headers.set("X-Request-ID", options.requestId);
    }
    const timeoutMs = options.timeoutMs ?? this.#timeoutMs;
    const idempotent = method === "GET";

    for (let attempt = 0; ; attempt += 1) {
      const outcome = await this.#attempt(method, url, headers, body, timeoutMs, options.signal);
      if (outcome.kind === "failure") {
        // A read is always safe to repeat. A write is safe only if the failure came before
        // anything was sent. After that the server may have done the work.
        const safe = outcome.retry && (idempotent || outcome.connectStage);
        if (safe && attempt < this.#maxRetries) {
          await this.#sleep(backoff(attempt, undefined) ?? 0);
          continue;
        }
        throw outcome.error;
      }
      const { status, headers: responseHeaders, parsed } = outcome;
      if (status >= 200 && status < 300) {
        if (!isRecord(parsed)) throw badResponse(status, responseHeaders, "not a JSON object");
        return { body: parsed, headers: responseHeaders };
      }
      if (status >= 300 && status < 400) {
        const redirect: { status: number; code: string; requestId?: string } = { status, code: "unexpected_redirect" };
        const requestId = responseHeaders.get("x-request-id");
        if (requestId) redirect.requestId = requestId;
        throw new ForgeError(
          "The server redirected the request. Redirects are not followed, so your API key is never " +
            "sent to another address. Check baseUrl.",
          redirect,
        );
      }
      const error = errorFromResponse(status, parsed, responseHeaders);
      if (attempt < this.#maxRetries && isRetryable(method, error)) {
        const delay = backoff(attempt, error.retryAfter);
        if (delay !== undefined) {
          await this.#sleep(delay);
          continue;
        }
      }
      throw error;
    }
  }

  async #attempt(
    method: string,
    url: string,
    headers: Headers,
    body: Blob | Uint8Array | string | undefined,
    timeoutMs: number,
    signal: AbortSignal | undefined,
  ): Promise<
    | { kind: "response"; status: number; headers: Headers; parsed: unknown }
    | { kind: "failure"; error: ForgeError; connectStage: boolean; retry: boolean }
  > {
    if (signal?.aborted) {
      return {
        kind: "failure",
        error: new ForgeAbortError("The request was aborted by the caller."),
        connectStage: false,
        retry: false,
      };
    }
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    const onAbort = (): void => controller.abort();
    signal?.addEventListener("abort", onAbort, { once: true });
    let started = false;
    try {
      const init: RequestInit = { method, headers, signal: controller.signal, redirect: "manual" };
      if (body !== undefined) init.body = body as BodyInit;
      const response = await this.#fetch(url, init);
      started = true;
      const status = response.status;
      const responseHeaders = response.headers;
      if (response.type === "opaqueredirect") {
        return { kind: "response", status: 302, headers: responseHeaders, parsed: undefined };
      }
      const text = await readBounded(response, MAX_RESPONSE_BYTES);
      if (text === null) {
        return {
          kind: "failure",
          error: badResponse(status, responseHeaders, "response too large"),
          connectStage: false,
          retry: false, // definitive: asking again returns the same oversized answer
        };
      }
      return { kind: "response", status, headers: responseHeaders, parsed: tryParse(text) };
    } catch (error) {
      if (signal?.aborted) {
        return {
          kind: "failure",
          error: new ForgeAbortError("The request was aborted by the caller.", { cause: error }),
          connectStage: false,
          retry: false, // the caller asked to stop
        };
      }
      if (timedOut) {
        return {
          kind: "failure",
          error: new ForgeTimeoutError(`The request to the Forge API timed out after ${timeoutMs} ms.`, { cause: error }),
          connectStage: false,
          retry: true,
        };
      }
      return {
        kind: "failure",
        error: new ForgeConnectionError("Could not reach the Forge API. Check baseUrl and your network.", {
          cause: error,
        }),
        connectStage: !started && isConnectStageError(error),
        retry: true,
      };
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    }
  }
}

function toRequestBody(audio: TranscribeParams["audio"]): { body: Blob | Uint8Array; size: number; type: string } {
  if (typeof Blob !== "undefined" && audio instanceof Blob) {
    return { body: audio, size: audio.size, type: audio.type };
  }
  let copy: Uint8Array;
  if (audio instanceof ArrayBuffer) {
    copy = new Uint8Array(audio.byteLength);
    copy.set(new Uint8Array(audio));
  } else if (ArrayBuffer.isView(audio)) {
    copy = new Uint8Array(audio.byteLength);
    copy.set(new Uint8Array(audio.buffer, audio.byteOffset, audio.byteLength));
  } else {
    throw new TypeError("audio must be a Blob, an ArrayBuffer or a typed array.");
  }
  return { body: copy, size: copy.byteLength, type: "" };
}

class Models {
  readonly #core: Core;
  constructor(core: Core) {
    this.#core = core;
  }

  /** The models available to this key. Needs the `models.read` scope. */
  async list(options: RequestOptions = {}): Promise<ModelList> {
    const { body, headers } = await this.#core.request("GET", "/models", {}, options);
    try {
      const items = body["data"];
      if (!Array.isArray(items)) throw new ShapeError("data");
      const data: Model[] = items.map((item: unknown) => {
        if (!isRecord(item)) throw new ShapeError("data[]");
        const languages = item["languages"];
        return {
          id: needString(item, "id"),
          kind: needString(item, "kind") as Model["kind"],
          displayName: needString(item, "display_name"),
          architecture: optString(item, "architecture"),
          parameters: optString(item, "parameters"),
          languages: Array.isArray(languages) ? languages.filter((x): x is string => typeof x === "string") : [],
          contextLengthTokens: optNumber(item, "context_length_tokens"),
          license: needString(item, "license"),
          sourceUrl: needString(item, "source_url"),
          attribution: optString(item, "attribution"),
          status: needString(item, "status") as Model["status"],
        };
      });
      return { requestId: headers.get("x-request-id") ?? "", data, rateLimit: rateLimitFrom(headers) };
    } catch (error) {
      if (error instanceof ShapeError) throw badResponse(200, headers, "models");
      throw error;
    }
  }
}

class Chat {
  readonly #core: Core;
  constructor(core: Core) {
    this.#core = core;
  }

  /** Generate a reply. Needs the `chat.create` scope. The server checks every value. */
  async create(params: ChatParams, options: RequestOptions = {}): Promise<ChatCompletion> {
    if (!Array.isArray(params?.messages) || params.messages.length === 0) {
      throw new TypeError("messages must be a non-empty array.");
    }
    for (const message of params.messages) {
      if (!isRecord(message) || typeof message["role"] !== "string" || typeof message["content"] !== "string") {
        throw new TypeError("Each message needs a string role and a string content.");
      }
    }
    const payload: Record<string, unknown> = {
      messages: params.messages.map((message) => ({ role: message.role, content: message.content })),
    };
    const optional: Array<[string, unknown]> = [
      ["model", params.model],
      ["max_tokens", params.maxTokens],
      ["temperature", params.temperature],
      ["top_p", params.topP],
      ["repetition_penalty", params.repetitionPenalty],
      ["language", params.language],
    ];
    for (const [name, value] of optional) {
      if (value !== undefined) payload[name] = value;
    }
    const { body, headers } = await this.#core.request("POST", "/chat", { jsonBody: payload }, options);
    try {
      const message = needRecord(body, "message");
      const usage = needRecord(body, "usage");
      const content = needString(message, "content");
      return {
        requestId: optString(body, "request_id") ?? headers.get("x-request-id") ?? "",
        model: needString(body, "model"),
        message: { role: "assistant", content },
        text: content,
        finishReason: needString(body, "finish_reason") as ChatCompletion["finishReason"],
        usage: {
          promptTokens: needNumber(usage, "prompt_tokens"),
          completionTokens: needNumber(usage, "completion_tokens"),
          totalTokens: needNumber(usage, "total_tokens"),
        },
        latencyMs: needNumber(body, "latency_ms"),
        rateLimit: rateLimitFrom(headers),
      };
    } catch (error) {
      if (error instanceof ShapeError) throw badResponse(200, headers, "chat");
      throw error;
    }
  }
}

class Audio {
  readonly #core: Core;
  constructor(core: Core) {
    this.#core = core;
  }

  /**
   * Transcribe speech in `en`, `ha`, `ig` or `yo`. Needs the `asr.transcribe` scope.
   *
   * The server accepts WAV, MP3, OGG, FLAC, MP4/M4A and WebM up to 10 MiB and 30 seconds. If the
   * deployment has no speech worker connected the call fails with code `provider_not_configured`.
   */
  async transcribe(params: TranscribeParams, options: RequestOptions = {}): Promise<Transcription> {
    const { body: audioBody, size, type } = toRequestBody(params.audio);
    if (size > MAX_AUDIO_BYTES) {
      throw new RangeError(`The audio is larger than ${MAX_AUDIO_BYTES / (1024 * 1024)} MiB.`);
    }
    const { body, headers } = await this.#core.request(
      "POST",
      "/audio/transcriptions",
      {
        query: { language: params.language },
        rawBody: audioBody,
        contentType: params.contentType ?? (type || "application/octet-stream"),
      },
      options,
    );
    try {
      const audio = needRecord(body, "audio");
      return {
        requestId: optString(body, "request_id") ?? headers.get("x-request-id") ?? "",
        language: needString(body, "language"),
        model: needString(body, "model"),
        text: needString(body, "text"),
        audio: {
          format: needString(audio, "format"),
          sizeBytes: needNumber(audio, "size_bytes"),
          durationSeconds: optNumber(audio, "duration_seconds"),
        },
        latencyMs: needNumber(body, "latency_ms"),
        attribution: optString(body, "attribution") ?? "",
        rateLimit: rateLimitFrom(headers),
      };
    } catch (error) {
      if (error instanceof ShapeError) throw badResponse(200, headers, "transcription");
      throw error;
    }
  }
}

/** A client for the Forge gateway. Create one and reuse it. */
export class Forge {
  readonly models: Models;
  readonly chat: Chat;
  readonly audio: Audio;
  readonly baseUrl: string;
  readonly #core: Core;

  constructor(options: ForgeOptions) {
    this.#core = new Core(options);
    this.baseUrl = this.#core.baseUrl;
    this.models = new Models(this.#core);
    this.chat = new Chat(this.#core);
    this.audio = new Audio(this.#core);
  }

  /** Build a client from `FORGE_API_KEY` and `FORGE_BASE_URL`, read from the object you pass. */
  static fromEnv(env: Record<string, string | undefined>, options: Partial<ForgeOptions> = {}): Forge {
    const apiKey = env["FORGE_API_KEY"];
    const baseUrl = env["FORGE_BASE_URL"];
    if (!apiKey || !baseUrl) throw new TypeError("Set FORGE_API_KEY and FORGE_BASE_URL.");
    return new Forge({ ...options, apiKey, baseUrl });
  }

  /** Never includes the key. */
  toJSON(): { baseUrl: string; apiKey: string } {
    return { baseUrl: this.baseUrl, apiKey: this.#core.keyHint };
  }

  toString(): string {
    return `Forge(${this.baseUrl}, key ${this.#core.keyHint})`;
  }
}
