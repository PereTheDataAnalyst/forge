/** Typed results. Every result that comes from the server has a `requestId`. */

export type Role = "system" | "user" | "assistant";
export type Language = "en" | "ha" | "ig" | "yo";

export interface ChatMessage {
  role: Role;
  content: string;
}

export interface RateLimit {
  /** Requests allowed in the current window. `undefined` if the server did not send it. */
  limit: number | undefined;
  remaining: number | undefined;
  resetSeconds: number | undefined;
}

export interface Usage {
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
}

export interface ChatCompletion {
  requestId: string;
  model: string;
  message: { role: "assistant"; content: string };
  /** The assistant's reply. The same string as `message.content`. */
  text: string;
  finishReason: "stop" | "length";
  usage: Usage;
  latencyMs: number;
  rateLimit: RateLimit;
}

export interface ChatParams {
  messages: readonly ChatMessage[];
  model?: string;
  maxTokens?: number;
  temperature?: number;
  topP?: number;
  repetitionPenalty?: number;
  /** Recorded in your logs and charts. It does not change the model. */
  language?: Language;
}

export interface Model {
  id: string;
  kind: "llm" | "asr";
  displayName: string;
  architecture: string | null;
  parameters: string | null;
  languages: string[];
  contextLengthTokens: number | null;
  license: string;
  sourceUrl: string;
  attribution: string | null;
  status: "available" | "unavailable" | "not_configured" | "loading";
}

export interface ModelList {
  requestId: string;
  data: Model[];
  rateLimit: RateLimit;
}

export interface TranscribeParams {
  /** The audio file: a Blob or File, an ArrayBuffer, or a typed array. */
  audio: Blob | ArrayBuffer | ArrayBufferView;
  language: Language;
  /** For example `audio/wav`. Defaults to the Blob's own type, then `application/octet-stream`. */
  contentType?: string;
}

export interface Transcription {
  requestId: string;
  language: string;
  model: string;
  text: string;
  audio: { format: string; sizeBytes: number; durationSeconds: number | null };
  latencyMs: number;
  /** Show this wherever you show the transcript. */
  attribution: string;
  rateLimit: RateLimit;
}

/** Options that apply to one call. */
export interface RequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
  /** Your own ID for this call, 8 to 64 letters, digits, dots, dashes or underscores. */
  requestId?: string;
}
