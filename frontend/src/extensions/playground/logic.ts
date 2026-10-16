/** Pure helpers for the playground. No React, no network, so they can be tested on their own. */

import type { ChatMessage, ChatRequestBody, LanguageCode } from "../../lib/types";

export interface Params {
  temperature: number;
  topP: number;
  maxTokens: number;
  repetitionPenalty: number;
}

export type ParamField = keyof Params;

/** Text values as typed in the form, so a half-typed number is never rewritten under the cursor. */
export type ParamFields = Record<ParamField, string>;

export type ParamErrors = Partial<Record<ParamField, string>>;

/** Defaults mirror the backend settings, which mirror the example in the official model card. */
export const DEFAULT_PARAMS: Params = {
  temperature: 0.1,
  topP: 1,
  maxTokens: 512,
  repetitionPenalty: 1.12,
};

/**
 * Bounds the backend enforces. `maxTokens` has a deployment cap (2048 by default, set by
 * NATLAS_MAX_TOKENS_LIMIT), so the form stops there and the server stays the authority.
 */
export const PARAM_LIMITS = {
  temperature: { min: 0, max: 2, step: 0.05 },
  topP: { min: 0.01, max: 1, step: 0.01 },
  maxTokens: { min: 1, max: 2048, step: 1 },
  repetitionPenalty: { min: 1, max: 2, step: 0.01 },
} as const;

export const PARAM_LABELS: Record<ParamField, string> = {
  temperature: "Temperature",
  topP: "Top p",
  maxTokens: "Maximum new tokens",
  repetitionPenalty: "Repetition penalty",
};

export function toFields(params: Params): ParamFields {
  return {
    temperature: String(params.temperature),
    topP: String(params.topP),
    maxTokens: String(params.maxTokens),
    repetitionPenalty: String(params.repetitionPenalty),
  };
}

function parseNumber(text: string): number | null {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

/** Parses the form text. Returns the numbers when every field is valid, otherwise the errors. */
export function parseParams(fields: ParamFields): { params: Params | null; errors: ParamErrors } {
  const errors: ParamErrors = {};
  const read = (field: ParamField): number => {
    const value = parseNumber(fields[field]);
    const { min, max } = PARAM_LIMITS[field];
    if (value === null) {
      errors[field] = "Enter a number.";
      return 0;
    }
    if (value < min || value > max) {
      errors[field] = `Use a value from ${min} to ${max}.`;
    }
    return value;
  };

  const temperature = read("temperature");
  const topP = read("topP");
  const maxTokens = read("maxTokens");
  const repetitionPenalty = read("repetitionPenalty");
  if (!errors.maxTokens && !Number.isInteger(maxTokens)) {
    errors.maxTokens = "Use a whole number.";
  }
  if (Object.keys(errors).length > 0) return { params: null, errors };
  return { params: { temperature, topP, maxTokens, repetitionPenalty }, errors };
}

export interface TurnMeta {
  requestId: string;
  model: string;
  finishReason: "stop" | "length";
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  gatewayLatencyMs: number;
  roundTripMs: number;
}

export interface Turn {
  id: string;
  role: "user" | "assistant";
  content: string;
  meta?: TurnMeta;
}

let turnCounter = 0;

export function nextTurnId(): string {
  turnCounter += 1;
  return `turn-${turnCounter}`;
}

/** The messages for one request: an optional system instruction, then the conversation. */
export function buildMessages(system: string, turns: readonly Turn[]): ChatMessage[] {
  const messages: ChatMessage[] = [];
  const instruction = system.trim();
  if (instruction) messages.push({ role: "system", content: instruction });
  for (const turn of turns) messages.push({ role: turn.role, content: turn.content });
  return messages;
}

export interface RequestInput {
  model: string;
  system: string;
  turns: readonly Turn[];
  params: Params;
  language: LanguageCode | "";
}

export function buildRequestBody(input: RequestInput): ChatRequestBody {
  const body: ChatRequestBody = {
    model: input.model || undefined,
    messages: buildMessages(input.system, input.turns),
    max_tokens: input.params.maxTokens,
    temperature: input.params.temperature,
    top_p: input.params.topP,
    repetition_penalty: input.params.repetitionPenalty,
  };
  if (input.language) body.language = input.language;
  return body;
}

/** The conversation as JSON, for copying out of the playground. */
export function exportConversation(system: string, turns: readonly Turn[]): string {
  return JSON.stringify({ messages: buildMessages(system, turns) }, null, 2);
}

export function describeFinish(reason: "stop" | "length"): string {
  return reason === "length" ? "Stopped at the token limit" : "Finished normally";
}
