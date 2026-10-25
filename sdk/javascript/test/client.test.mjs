// Tests for the JavaScript client. They talk to a real HTTP server on loopback: no mocks of the wire.
// Run `npm test` (it builds first) or `node --test test/client.test.mjs` after `npm run build`.
import assert from "node:assert/strict";
import http from "node:http";
import net from "node:net";
import { after, afterEach, beforeEach, describe, test } from "node:test";
import { inspect } from "node:util";

import {
  Forge,
  ForgeAbortError,
  ForgeAuthenticationError,
  ForgeConnectionError,
  ForgeError,
  ForgeInvalidRequestError,
  ForgeNotFoundError,
  ForgePermissionError,
  ForgeRateLimitError,
  ForgeServerError,
  ForgeTimeoutError,
  VERSION,
} from "../dist/index.js";

const KEY = "nfk_0123456789_" + "A".repeat(43);
const CHAT_BODY = {
  request_id: "req_chat0000000001",
  model: "NCAIR1/N-ATLaS",
  message: { role: "assistant", content: "Bawo ni" },
  finish_reason: "stop",
  usage: { prompt_tokens: 12, completion_tokens: 30, total_tokens: 42 },
  latency_ms: 1840,
};
const VOICE_BODY = {
  request_id: "req_voice000000001",
  language: "yo",
  model: "NCAIR1/Yoruba-ASR",
  text: "Bawo ni",
  audio: { format: "wav", size_bytes: 5, duration_seconds: 1.0 },
  latency_ms: 900,
  attribution: "Yoruba-ASR is developed by NCAIR",
};
const MODEL_ITEM = {
  id: "NCAIR1/N-ATLaS",
  kind: "llm",
  display_name: "N-ATLaS LLM",
  architecture: "Llama-3 8B",
  parameters: "8B",
  languages: ["en", "ha", "ig", "yo"],
  context_length_tokens: 8092,
  license: "N-ATLaS Terms of Use",
  source_url: "https://huggingface.co/NCAIR1/N-ATLaS",
  attribution: "N-ATLaS is an initiative of NCAIR",
  status: "available",
};

class FakeServer {
  constructor() {
    this.script = [];
    this.seen = [];
    this.server = http.createServer((req, res) => {
      const chunks = [];
      req.on("data", (chunk) => chunks.push(chunk));
      req.on("end", () => {
        this.seen.push({ method: req.method, path: req.url, headers: req.headers, body: Buffer.concat(chunks) });
        const [status, headers, payload, delay] = this.script.shift() ?? [500, {}, {}, 0];
        const send = () => {
          const data = Buffer.isBuffer(payload) ? payload : Buffer.from(JSON.stringify(payload));
          res.writeHead(status, { "Content-Type": "application/json", "Content-Length": data.length, ...headers });
          res.end(data);
        };
        delay ? setTimeout(send, delay) : send();
      });
    });
  }
  async start() {
    await new Promise((resolve) => this.server.listen(0, "127.0.0.1", resolve));
    this.url = `http://127.0.0.1:${this.server.address().port}`;
    return this;
  }
  reply(status = 200, body = {}, headers = {}, delay = 0) {
    this.script.push([status, headers, body, delay]);
  }
  async close() {
    this.server.closeAllConnections?.();
    await new Promise((resolve) => this.server.close(resolve));
  }
}

const header = (seen, name) => seen.headers[name.toLowerCase()];
const userMessage = (content = "Hi") => ({ messages: [{ role: "user", content }] });

describe("Forge client", () => {
  let server;
  let slept;
  let forge;
  const extras = [];

  beforeEach(async () => {
    server = await new FakeServer().start();
    slept = [];
    forge = new Forge({ apiKey: KEY, baseUrl: server.url, maxRetries: 2, sleep: async (ms) => void slept.push(ms) });
  });
  afterEach(async () => {
    await server.close();
    while (extras.length) await extras.pop().close();
  });
  after(() => {});

  test("chat sends only what was given and authenticates", async () => {
    server.reply(200, CHAT_BODY, { "X-RateLimit-Limit": "60", "X-RateLimit-Remaining": "59", "X-RateLimit-Reset": "31" });
    const reply = await forge.chat.create({
      messages: [{ role: "user", content: "Ẹ kú àárọ̀" }],
      language: "yo",
      maxTokens: 64,
      temperature: 0,
    });
    const [seen] = server.seen;
    assert.equal(seen.method, "POST");
    assert.equal(seen.path, "/api/v1/chat");
    assert.deepEqual(JSON.parse(seen.body.toString("utf8")), {
      messages: [{ role: "user", content: "Ẹ kú àárọ̀" }],
      language: "yo",
      max_tokens: 64,
      temperature: 0,
    });
    assert.equal(header(seen, "authorization"), `Bearer ${KEY}`);
    assert.match(header(seen, "content-type"), /^application\/json/);
    assert.match(header(seen, "user-agent"), new RegExp(`natlas-forge-js/${VERSION}`));
    assert.equal(header(seen, "x-request-id"), undefined);
    assert.equal(reply.text, "Bawo ni");
    assert.equal(reply.message.content, "Bawo ni");
    assert.equal(reply.requestId, "req_chat0000000001");
    assert.deepEqual(reply.usage, { promptTokens: 12, completionTokens: 30, totalTokens: 42 });
    assert.equal(reply.latencyMs, 1840);
    assert.equal(reply.finishReason, "stop");
    assert.deepEqual(reply.rateLimit, { limit: 60, remaining: 59, resetSeconds: 31 });
  });

  test("messages are checked locally and nothing bad is sent", async () => {
    for (const bad of [{ messages: [] }, { messages: "hello" }, { messages: [{ role: "user" }] }, { messages: [42] }, {}]) {
      await assert.rejects(forge.chat.create(bad), TypeError);
    }
    assert.equal(server.seen.length, 0);
  });

  test("a caller chosen request id is sent and checked", async () => {
    server.reply(200, CHAT_BODY);
    await forge.chat.create(userMessage(), { requestId: "my-trace_0001" });
    assert.equal(header(server.seen[0], "x-request-id"), "my-trace_0001");
    for (const bad of ["short", "has space 12345", "x".repeat(65), "bad\r\nHeader: 1"]) {
      await assert.rejects(forge.chat.create(userMessage(), { requestId: bad }), TypeError);
    }
    assert.equal(server.seen.length, 1);
  });

  test("models list", async () => {
    server.reply(200, { data: [MODEL_ITEM] }, { "X-Request-ID": "req_models00000001" });
    const result = await forge.models.list();
    assert.equal(server.seen[0].method, "GET");
    assert.equal(server.seen[0].path, "/api/v1/models");
    assert.equal(result.requestId, "req_models00000001");
    const [model] = result.data;
    assert.deepEqual([model.id, model.kind, model.status, model.displayName], ["NCAIR1/N-ATLaS", "llm", "available", "N-ATLaS LLM"]);
    assert.deepEqual(model.languages, ["en", "ha", "ig", "yo"]);
    assert.equal(model.contextLengthTokens, 8092);
  });

  test("transcribe sends the audio as the raw body", async () => {
    server.reply(200, VOICE_BODY);
    const result = await forge.audio.transcribe({ audio: new Uint8Array([82, 73, 70, 70, 1]), language: "yo", contentType: "audio/wav" });
    const [seen] = server.seen;
    assert.equal(seen.path, "/api/v1/audio/transcriptions?language=yo");
    assert.deepEqual([...seen.body], [82, 73, 70, 70, 1]);
    assert.equal(header(seen, "content-type"), "audio/wav");
    assert.equal(result.text, "Bawo ni");
    assert.equal(result.requestId, "req_voice000000001");
    assert.deepEqual(result.audio, { format: "wav", sizeBytes: 5, durationSeconds: 1 });
    assert.match(result.attribution, /NCAIR/);
  });

  test("transcribe accepts a Blob, an ArrayBuffer and a typed array view, and picks the content type", async () => {
    server.reply(200, VOICE_BODY);
    await forge.audio.transcribe({ audio: new Blob([new Uint8Array([1, 2, 3])], { type: "audio/ogg" }), language: "ha" });
    server.reply(200, VOICE_BODY);
    await forge.audio.transcribe({ audio: new Uint8Array([9, 9]).buffer, language: "ha" });
    server.reply(200, VOICE_BODY);
    const big = new Uint8Array([0, 1, 2, 3, 4, 5]);
    await forge.audio.transcribe({ audio: big.subarray(2, 5), language: "ig", contentType: "audio/mp4" });
    assert.deepEqual(server.seen.map((s) => header(s, "content-type")), ["audio/ogg", "application/octet-stream", "audio/mp4"]);
    assert.deepEqual(server.seen.map((s) => [...s.body]), [[1, 2, 3], [9, 9], [2, 3, 4]]);
    await assert.rejects(forge.audio.transcribe({ audio: "not audio", language: "yo" }), TypeError);
    assert.equal(server.seen.length, 3);
  });

  test("a language is encoded in the query", async () => {
    server.reply(200, VOICE_BODY);
    await forge.audio.transcribe({ audio: new Uint8Array([1]), language: "yo&admin=1" });
    assert.equal(server.seen[0].path, "/api/v1/audio/transcriptions?language=yo%26admin%3D1");
  });

  const error = (status, code, message = "Refused.", headers = {}, details = undefined) =>
    server.reply(status, { error: { code, message, request_id: "req_err00000000001", ...(details ? { details } : {}) } }, headers);

  test("each status becomes its own error class with the request id", async () => {
    const cases = [
      [401, "invalid_api_key", ForgeAuthenticationError],
      [403, "insufficient_scope", ForgePermissionError],
      [404, "model_not_found", ForgeNotFoundError],
      [413, "payload_too_large", ForgeInvalidRequestError],
      [422, "validation_error", ForgeInvalidRequestError],
    ];
    for (const [status, code, Kind] of cases) {
      error(status, code);
      await assert.rejects(forge.chat.create(userMessage()), (caught) => {
        assert.ok(caught instanceof Kind && caught instanceof ForgeError);
        assert.deepEqual([caught.status, caught.code, caught.requestId], [status, code, "req_err00000000001"]);
        assert.match(String(caught), /req_err00000000001/);
        return true;
      });
    }
  });

  test("validation details are kept", async () => {
    error(422, "validation_error", "Bad.", {}, [{ field: "messages", message: "required" }]);
    await assert.rejects(forge.chat.create(userMessage()), (caught) => {
      assert.deepEqual(caught.details, [{ field: "messages", message: "required" }]);
      return true;
    });
  });

  test("the request id header is used when the body has none", async () => {
    server.reply(502, Buffer.from("<html>bad gateway</html>"), { "X-Request-ID": "req_proxy0000000001" });
    const noRetry = new Forge({ apiKey: KEY, baseUrl: server.url, maxRetries: 0 });
    await assert.rejects(noRetry.chat.create(userMessage()), (caught) => {
      assert.ok(caught instanceof ForgeServerError);
      assert.equal(caught.requestId, "req_proxy0000000001");
      assert.equal(caught.code, undefined);
      return true;
    });
  });

  test("a success that is not the expected shape is a clear error", async () => {
    server.reply(200, { unexpected: true }, { "X-Request-ID": "req_odd000000000001" });
    await assert.rejects(forge.chat.create(userMessage()), (caught) => {
      assert.equal(caught.code, "invalid_response");
      assert.equal(caught.requestId, "req_odd000000000001");
      return true;
    });
    server.reply(200, Buffer.from("not json at all"));
    await assert.rejects(forge.models.list(), (caught) => caught.code === "invalid_response");
  });

  test("a rate limit is retried after the delay the server asked for", async () => {
    error(429, "rate_limited", "Slow.", { "Retry-After": "7" });
    server.reply(200, CHAT_BODY);
    const reply = await forge.chat.create(userMessage());
    assert.equal(reply.text, "Bawo ni");
    assert.equal(server.seen.length, 2);
    assert.deepEqual(slept, [7000]);
  });

  test("a long retry-after is not waited for", async () => {
    error(429, "rate_limited", "Slow.", { "Retry-After": "120" });
    await assert.rejects(forge.chat.create(userMessage()), (caught) => {
      assert.ok(caught instanceof ForgeRateLimitError);
      assert.equal(caught.retryAfter, 120);
      return true;
    });
    assert.deepEqual([server.seen.length, slept], [1, []]);
  });

  test("retries are bounded and back off", async () => {
    for (let n = 0; n < 5; n += 1) error(503, "provider_unavailable");
    await assert.rejects(forge.chat.create(userMessage()), ForgeServerError);
    assert.equal(server.seen.length, 3);
    assert.equal(slept.length, 2);
    assert.ok(slept[0] >= 250 && slept[0] <= 500 && slept[1] >= 500 && slept[1] <= 1000, String(slept));
  });

  test("maxRetries zero means one try", async () => {
    const once = new Forge({ apiKey: KEY, baseUrl: server.url, maxRetries: 0, sleep: async (ms) => void slept.push(ms) });
    error(429, "rate_limited");
    await assert.rejects(once.chat.create(userMessage()), ForgeRateLimitError);
    assert.deepEqual([server.seen.length, slept], [1, []]);
  });

  test("a write is not repeated when the model may have run", async () => {
    for (const status of [502, 504, 500]) {
      server.seen.length = 0;
      error(status, status === 504 ? "provider_timeout" : "provider_error");
      await assert.rejects(forge.chat.create(userMessage()), ForgeServerError);
      assert.equal(server.seen.length, 1, String(status));
    }
  });

  test("a read is repeated on 502 and 504 too", async () => {
    error(504, "provider_timeout");
    server.reply(200, { data: [MODEL_ITEM] });
    assert.equal((await forge.models.list()).data.length, 1);
    assert.equal(server.seen.length, 2);
  });

  test("a 503 that waiting cannot fix is not retried", async () => {
    error(503, "provider_not_configured");
    await assert.rejects(forge.audio.transcribe({ audio: new Uint8Array([1]), language: "yo" }), (caught) => {
      assert.equal(caught.code, "provider_not_configured");
      return true;
    });
    assert.equal(server.seen.length, 1);
  });

  test("a refused connection is a connection error, and a write may be retried safely", async () => {
    const probe = net.createServer();
    await new Promise((resolve) => probe.listen(0, "127.0.0.1", resolve));
    const port = probe.address().port;
    await new Promise((resolve) => probe.close(resolve));
    const lonely = new Forge({ apiKey: KEY, baseUrl: `http://127.0.0.1:${port}`, maxRetries: 2, sleep: async (ms) => void slept.push(ms) });
    await assert.rejects(lonely.chat.create(userMessage()), (caught) => {
      assert.ok(caught instanceof ForgeConnectionError && !(caught instanceof ForgeTimeoutError));
      assert.ok(!String(caught).includes(KEY));
      return true;
    });
    assert.equal(slept.length, 2); // nothing was sent, so repeating cannot do it twice
  });

  test("a timeout is not retried for a write but is for a read", async () => {
    const quick = new Forge({ apiKey: KEY, baseUrl: server.url, timeoutMs: 200, maxRetries: 2, sleep: async (ms) => void slept.push(ms) });
    server.reply(200, CHAT_BODY, {}, 700);
    await assert.rejects(quick.chat.create(userMessage()), ForgeTimeoutError);
    assert.deepEqual([server.seen.length, slept], [1, []]);
    server.seen.length = 0;
    server.reply(200, { data: [] }, {}, 700);
    server.reply(200, { data: [MODEL_ITEM] });
    assert.equal((await quick.models.list()).data.length, 1);
    assert.equal(server.seen.length, 2);
  });

  test("the caller can abort a request", async () => {
    server.reply(200, CHAT_BODY, {}, 700);
    const controller = new AbortController();
    setTimeout(() => controller.abort(), 100);
    await assert.rejects(forge.chat.create(userMessage(), { signal: controller.signal }), ForgeAbortError);
    const already = new AbortController();
    already.abort();
    await assert.rejects(forge.models.list({ signal: already.signal }), ForgeAbortError);
    assert.equal(slept.length, 0);
  });

  test("a redirect is never followed so the key stays put", async () => {
    const other = await new FakeServer().start();
    extras.push(other);
    other.reply(200, CHAT_BODY);
    server.reply(302, {}, { Location: `${other.url}/api/v1/chat` });
    await assert.rejects(forge.chat.create(userMessage()), (caught) => {
      assert.equal(caught.code, "unexpected_redirect");
      return true;
    });
    assert.equal(other.seen.length, 0);
    assert.equal(server.seen.length, 1);
  });

  test("an oversized response is refused", async () => {
    server.reply(200, Buffer.concat([Buffer.from("{"), Buffer.alloc(8 * 1024 * 1024 + 10, 32), Buffer.from("}")]));
    await assert.rejects(forge.models.list(), (caught) => caught.code === "invalid_response");
  });

  test("the key is never shown", async () => {
    for (const text of [String(forge), JSON.stringify(forge), inspect(forge, { depth: 6, showHidden: true })]) {
      assert.ok(!text.includes(KEY), text);
    }
    assert.match(String(forge), /nfk_0123/);
    error(401, "invalid_api_key", "The API key is not valid.");
    await assert.rejects(forge.models.list(), (caught) => {
      assert.ok(!(String(caught) + inspect(caught, { depth: 6 })).includes(KEY));
      return true;
    });
  });

  test("bad configuration is refused before anything is sent", () => {
    const bad = [
      { apiKey: "", baseUrl: server.url },
      { apiKey: "nfk with space", baseUrl: server.url },
      { apiKey: "nfk_abc\r\nX: y", baseUrl: server.url },
      { apiKey: KEY, baseUrl: "" },
      { apiKey: KEY, baseUrl: "not a url" },
      { apiKey: KEY, baseUrl: "ftp://example.com" },
      { apiKey: KEY, baseUrl: "https://user:pw@example.com" },
      { apiKey: KEY, baseUrl: "https://example.com?x=1" },
      { apiKey: KEY, baseUrl: "http://example.com" },
      { apiKey: KEY, baseUrl: server.url, timeoutMs: 0 },
      { apiKey: KEY, baseUrl: server.url, maxRetries: -1 },
      { apiKey: KEY, baseUrl: server.url, maxRetries: 99 },
      { apiKey: KEY, baseUrl: server.url, maxRetries: 1.5 },
      { apiKey: KEY, baseUrl: server.url, fetch: "nope" },
    ];
    for (const options of bad) assert.throws(() => new Forge(options), TypeError, JSON.stringify(options));
    assert.equal(server.seen.length, 0);
  });

  test("plain http to a remote host needs an explicit switch", () => {
    new Forge({ apiKey: KEY, baseUrl: "http://example.com", allowInsecureHttp: true });
    new Forge({ apiKey: KEY, baseUrl: "http://localhost:8000" });
    new Forge({ apiKey: KEY, baseUrl: "http://[::1]:8000" });
  });

  test("the base url is normalised", () => {
    for (const [given, expected] of [
      ["https://forge.example.com", "https://forge.example.com/api/v1"],
      ["https://forge.example.com/", "https://forge.example.com/api/v1"],
      ["https://forge.example.com/api/v1", "https://forge.example.com/api/v1"],
      ["https://forge.example.com/api/v1/", "https://forge.example.com/api/v1"],
      ["https://example.com/forge", "https://example.com/forge/api/v1"],
      ["http://127.0.0.1:8000", "http://127.0.0.1:8000/api/v1"],
    ]) {
      assert.equal(new Forge({ apiKey: KEY, baseUrl: given }).baseUrl, expected);
    }
  });

  test("settings can come from an environment object", async () => {
    server.reply(200, { data: [MODEL_ITEM] });
    const client = Forge.fromEnv({ FORGE_API_KEY: KEY, FORGE_BASE_URL: server.url });
    assert.equal((await client.models.list()).data.length, 1);
    assert.throws(() => Forge.fromEnv({}), TypeError);
    assert.throws(() => Forge.fromEnv({ FORGE_API_KEY: KEY }), TypeError);
  });

  test("a custom fetch replaces the network", async () => {
    const calls = [];
    const custom = new Forge({
      apiKey: KEY,
      baseUrl: "https://forge.example.com",
      fetch: async (url, init) => {
        calls.push([url, init.method, init.redirect]);
        return new Response(JSON.stringify({ data: [MODEL_ITEM] }), { status: 200, headers: { "x-request-id": "req_custom0000001" } });
      },
    });
    const result = await custom.models.list();
    assert.equal(result.requestId, "req_custom0000001");
    assert.deepEqual(calls, [["https://forge.example.com/api/v1/models", "GET", "manual"]]);
  });
});
