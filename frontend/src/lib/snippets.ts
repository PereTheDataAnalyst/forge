/** Builds request examples for the gateway endpoint that exists today: POST /api/v1/chat. */

export interface ChatSnippetInput {
  baseUrl: string;
  model: string;
  prompt: string;
  language: string;
  temperature: number;
  maxTokens: number;
}

export type SnippetLanguage = "curl" | "powershell" | "javascript" | "python";

export function requestBody(input: ChatSnippetInput): Record<string, unknown> {
  return {
    model: input.model,
    messages: [{ role: "user", content: input.prompt }],
    max_tokens: input.maxTokens,
    temperature: input.temperature,
    language: input.language,
  };
}

function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

function powershellQuote(value: string): string {
  return `'${value.replace(/'/g, "''")}'`;
}

export function buildSnippet(kind: SnippetLanguage, input: ChatSnippetInput): string {
  const url = `${input.baseUrl}/api/v1/chat`;
  const body = requestBody(input);
  const compact = JSON.stringify(body);
  const pretty = JSON.stringify(body, null, 2);

  switch (kind) {
    case "curl":
      return [
        `curl ${shellQuote(url)} \\`,
        `  -H "Authorization: Bearer $FORGE_API_KEY" \\`,
        `  -H "Content-Type: application/json" \\`,
        `  -d ${shellQuote(compact)}`,
      ].join("\n");
    case "powershell":
      return [
        `$body = ${powershellQuote(compact)}`,
        `Invoke-RestMethod -Method Post -Uri ${powershellQuote(url)} \``,
        `  -Headers @{ Authorization = "Bearer $env:FORGE_API_KEY" } \``,
        `  -ContentType "application/json; charset=utf-8" -Body $body`,
      ].join("\n");
    case "javascript":
      return [
        `const response = await fetch(${JSON.stringify(url)}, {`,
        `  method: "POST",`,
        `  headers: {`,
        `    Authorization: \`Bearer \${process.env.FORGE_API_KEY}\`,`,
        `    "Content-Type": "application/json",`,
        `  },`,
        `  body: JSON.stringify(${pretty.replace(/\n/g, "\n  ")}),`,
        `});`,
        `const result = await response.json();`,
        `console.log(result.message.content);`,
      ].join("\n");
    case "python":
      return [
        `import os`,
        `import requests`,
        ``,
        `response = requests.post(`,
        `    ${JSON.stringify(url)},`,
        `    headers={"Authorization": f"Bearer {os.environ['FORGE_API_KEY']}"},`,
        `    json=${pretty.replace(/\n/g, "\n    ")},`,
        `    timeout=120,`,
        `)`,
        `response.raise_for_status()`,
        `print(response.json()["message"]["content"])`,
      ].join("\n");
  }
}

/** Shape of a successful response. Values in angle brackets are placeholders, not model output. */
export const RESPONSE_SHAPE = `{
  "request_id": "req_<24 hex characters>",
  "model": "NCAIR1/N-ATLaS",
  "message": {
    "role": "assistant",
    "content": "<generated text>"
  },
  "finish_reason": "stop | length",
  "usage": {
    "prompt_tokens": <integer>,
    "completion_tokens": <integer>,
    "total_tokens": <integer>
  },
  "latency_ms": <integer>
}`;
