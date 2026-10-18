import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { HERE, playwright } from "./paths.mjs";
import { startServer, json, readBody } from "./server.mjs";

const { chromium } = playwright();
process.chdir(HERE); // screenshots go to ./shots
fs.mkdirSync(path.join(HERE, "shots"), { recursive: true });

const PROJECTS = [
  { id: "11111111-1111-4111-8111-111111111111", name: "Harness project", description: null, created_at: "2026-10-15T09:00:00Z", updated_at: "2026-10-15T09:00:00Z", active_key_count: 1 },
  { id: "22222222-2222-4222-8222-222222222222", name: "Second project", description: null, created_at: "2026-10-15T09:30:00Z", updated_at: "2026-10-15T09:30:00Z", active_key_count: 0 },
];
const state = { chats: [], aborted: 0, modelStatus: "available", projects: PROJECTS, counter: 0 };

function model() {
  return { id: "NCAIR1/N-ATLaS", kind: "llm", display_name: "N-ATLaS LLM", architecture: "Llama-3 8B", parameters: "8B", languages: ["en", "ha", "ig", "yo"], context_length_tokens: 8092, license: "N-ATLaS Terms of Use", source_url: "https://huggingface.co/NCAIR1/N-ATLaS", attribution: "x", status: state.modelStatus };
}

async function api(req, res, url) {
  const p = url.pathname;
  if (p === "/api/v1/auth/me") return json(res, 200, { id: "u1", email: "dev@example.com", display_name: "Dev", created_at: "2026-10-15T09:00:00Z" });
  if (p === "/api/v1/projects") return json(res, 200, { data: state.projects });
  if (p.endsWith("/playground/models")) return json(res, 200, { data: [model()] });
  if (p.endsWith("/playground/chat") && req.method === "POST") {
    const body = await readBody(req);
    state.chats.push({ headers: req.headers, body, path: p });
    const last = body.messages[body.messages.length - 1].content;
    if (last.startsWith("FAIL")) {
      return json(res, 503, { error: { code: "provider_unavailable", message: "The N-ATLAS inference service is not reachable. Try again shortly.", request_id: "req_failfailfail" } }, { "x-request-id": "req_failfailfail" });
    }
    const reply = () => {
      state.counter += 1;
      const id = `req_mock${String(state.counter).padStart(4, "0")}`;
      json(res, 200, { request_id: id, model: "NCAIR1/N-ATLaS", message: { role: "assistant", content: `Mock reply to: ${last}` }, finish_reason: last.startsWith("LONG") ? "length" : "stop", usage: { prompt_tokens: 12, completion_tokens: 8, total_tokens: 20 }, latency_ms: 431 }, { "x-request-id": id });
    };
    if (last.startsWith("SLOW")) {
      const timer = setTimeout(reply, 6000);
      res.on("close", () => { if (!res.writableFinished) { clearTimeout(timer); state.aborted += 1; } });
      return;
    }
    return reply();
  }
  json(res, 404, { error: { code: "not_found", message: "no such mock route", request_id: null } });
}

const results = [];
async function step(name, fn) {
  try { await fn(); results.push(["PASS", name]); console.log("PASS", name); }
  catch (error) { results.push(["FAIL", name]); console.log("FAIL", name, "\n   ", String(error.message).split("\n").slice(0, 6).join("\n    ")); }
}

const { server, port } = await startServer(api);
const base = `http://127.0.0.1:${port}`;
const browser = await chromium.launch({ args: ["--no-sandbox"] });
const consoleProblems = [];
function watch(page) {
  page.on("console", (m) => { if (["error", "warning"].includes(m.type())) consoleProblems.push(`${m.type()}: ${m.text()}`); });
  page.on("pageerror", (e) => consoleProblems.push(`pageerror: ${e.message}`));
}

const desktop = await browser.newPage({ viewport: { width: 1600, height: 900 } });
watch(desktop);
await desktop.goto(`${base}/?page=playground`);
const send = desktop.getByRole("button", { name: "Send", exact: true });
const message = desktop.getByLabel("Message", { exact: true });

await step("page renders with project, model and status", async () => {
  await desktop.getByRole("heading", { name: "Playground", level: 1 }).waitFor();
  await desktop.getByText("Connected", { exact: true }).waitFor();
  assert.equal(await desktop.getByLabel("Project", { exact: true }).inputValue(), PROJECTS[0].id);
  await desktop.waitForFunction(() => document.querySelector("select[id$='-model']")?.value === "NCAIR1/N-ATLaS");
  assert.equal(await send.isDisabled(), true);
  await desktop.screenshot({ path: "shots/pg-desktop-initial.png" });
});

await step("sending posts the real request with CSRF and shows the reply", async () => {
  await message.fill("Hello N-ATLAS");
  await send.click();
  await desktop.getByText("Mock reply to: Hello N-ATLAS").waitFor();
  const call = state.chats[0];
  assert.equal(call.headers["x-csrf-token"], "harness-csrf");
  assert.match(call.headers["content-type"], /application\/json/);
  assert.equal(call.path, `/api/v1/projects/${PROJECTS[0].id}/playground/chat`);
  assert.deepEqual(call.body.messages, [{ role: "user", content: "Hello N-ATLAS" }]);
  assert.equal(call.body.model, "NCAIR1/N-ATLaS");
  assert.equal(call.body.max_tokens, 512);
  assert.equal(call.body.temperature, 0.1);
  assert.equal(call.body.top_p, 1);
  assert.equal(call.body.repetition_penalty, 1.12);
  assert.equal("language" in call.body, false);
  assert.equal(await message.inputValue(), "");
  await desktop.getByText("req_mock0001").first().waitFor();
});

await step("details show the request, the raw response and the metadata", async () => {
  await desktop.getByRole("button", { name: "Request", exact: true }).click();
  await desktop.getByText('"messages"').first().waitFor();
  await desktop.getByRole("button", { name: "Response", exact: true }).click();
  await desktop.getByText("Raw response").waitFor();
  await desktop.getByText("12 in, 8 out, 20 total").waitFor();
  await desktop.getByText("431 ms").waitFor();
});

await step("system instruction, language and history are sent", async () => {
  await desktop.getByLabel("System instruction").fill("Answer in Yoruba.");
  await desktop.getByLabel("Language", { exact: true }).selectOption("yo");
  await message.fill("Second question");
  await desktop.keyboard.press("Tab"); // leave the field, then use the keyboard shortcut
  await message.focus();
  await desktop.keyboard.press("Control+Enter");
  await desktop.locator(".pg-msg__body", { hasText: "Mock reply to: Second question" }).waitFor();
  const body = state.chats[1].body;
  assert.deepEqual(body.messages.map((m) => m.role), ["system", "user", "assistant", "user"]);
  assert.equal(body.messages[0].content, "Answer in Yoruba.");
  assert.equal(body.language, "yo");
});

await step("regenerate resends the last question without the old answer", async () => {
  const before = state.chats.length;
  await desktop.getByRole("button", { name: "Regenerate" }).click();
  await desktop.waitForFunction((n) => true, before);
  await desktop.waitForTimeout(400);
  assert.equal(state.chats.length, before + 1);
  const body = state.chats[before].body;
  assert.deepEqual(body.messages.map((m) => m.role), ["system", "user", "assistant", "user"]);
  assert.equal(body.messages[3].content, "Second question");
  await desktop.screenshot({ path: "shots/pg-desktop-conversation.png" });
});

await step("invalid parameters block the request and name the field", async () => {
  const before = state.chats.length;
  await desktop.getByLabel("Temperature", { exact: true }).fill("5");
  await message.fill("Parameter check");
  await send.click();
  await desktop.getByText("Use a value from 0 to 2.").waitFor();
  assert.equal(state.chats.length, before);
  await desktop.getByRole("button", { name: "Reset parameters" }).click();
  assert.equal(await desktop.getByLabel("Temperature", { exact: true }).inputValue(), "0.1");
  await message.fill("");
});

await step("a provider error shows the message and request ID and keeps the draft", async () => {
  const turnsBefore = await desktop.locator(".pg-msg").count();
  await message.fill("FAIL please");
  await send.click();
  await desktop.locator(".notice__message", { hasText: "The N-ATLAS inference service is not reachable. Try again shortly." }).waitFor();
  await desktop.getByText("Request ID: req_failfailfail").waitFor();
  assert.equal(await message.inputValue(), "FAIL please");
  assert.equal(await desktop.locator(".pg-msg").count(), turnsBefore);
  await desktop.getByRole("button", { name: "Response", exact: true }).click();
  await desktop.getByText("Error response").waitFor();
  await desktop.getByText('"code": "provider_unavailable"').waitFor();
  await message.fill("");
});

await step("stop cancels the wait, restores the draft and drops the unanswered turn", async () => {
  const turnsBefore = await desktop.locator(".pg-msg").count();
  await message.fill("SLOW please");
  await send.click();
  const stop = desktop.getByRole("button", { name: "Stop", exact: true });
  await stop.waitFor();
  await desktop.getByText("Waiting for N-ATLAS. A long reply can take a minute.").waitFor();
  await stop.click();
  await desktop.getByText(/Stopped\. Your browser stopped waiting/).waitFor();
  assert.equal(await message.inputValue(), "SLOW please");
  assert.equal(await desktop.locator(".pg-msg").count(), turnsBefore);
  await desktop.waitForTimeout(300);
  assert.equal(state.aborted, 1);
  assert.equal(await stop.count(), 0);
  await message.fill("");
});

await step("generated code reflects the last request in four languages", async () => {
  await desktop.getByRole("button", { name: "Code", exact: true }).click();
  await desktop.getByText(/curl 'http:\/\/127\.0\.0\.1:\d+\/api\/v1\/chat'/).waitFor();
  const curl = await desktop.locator(".pg-details pre").innerText();
  assert.match(curl, /FORGE_API_KEY/);
  assert.match(curl, /Second question/);
  assert.match(curl, /"repetition_penalty":1\.12/);
  await desktop.getByRole("button", { name: "PowerShell", exact: true }).click();
  assert.match(await desktop.locator(".pg-details pre").innerText(), /Invoke-RestMethod/);
  await desktop.getByRole("button", { name: "JavaScript", exact: true }).click();
  assert.match(await desktop.locator(".pg-details pre").innerText(), /await fetch\(/);
  await desktop.getByRole("button", { name: "Python", exact: true }).click();
  assert.match(await desktop.locator(".pg-details pre").innerText(), /requests\.post\(/);
  await desktop.screenshot({ path: "shots/pg-desktop-code.png" });
});

await step("length finish is reported honestly", async () => {
  await message.fill("LONG answer");
  await send.click();
  await desktop.getByText("Stopped at the token limit").first().waitFor();
});

await step("clear empties the conversation", async () => {
  await desktop.getByRole("button", { name: "Clear", exact: true }).click();
  await desktop.getByText(/No messages yet\./).waitFor();
  assert.equal(await desktop.locator(".pg-msg").count(), 0);
});

await step("hiding details widens the conversation on desktop", async () => {
  const before = (await desktop.locator(".pg__chat").boundingBox()).width;
  await desktop.getByRole("button", { name: "Hide", exact: true }).click();
  const after = (await desktop.locator(".pg__chat").boundingBox()).width;
  assert.ok(after > before + 200, `chat width ${before} -> ${after}`);
  await desktop.getByRole("button", { name: "Show details" }).click();
  await desktop.getByRole("heading", { name: "Details", level: 2 }).waitFor();
});

await step("a model host that is away shows a banner", async () => {
  state.modelStatus = "unavailable";
  await desktop.reload();
  await desktop.getByText(/The model host is not connected right now \(unreachable\)\./).waitFor();
  state.modelStatus = "available";
});

await step("no projects shows an honest empty state", async () => {
  state.projects = [];
  await desktop.reload();
  await desktop.getByText("Create a project first").waitFor();
  assert.equal(await desktop.locator(".pg").count(), 0);
  state.projects = PROJECTS;
});

const laptop = await browser.newPage({ viewport: { width: 1366, height: 800 } });
watch(laptop);
await laptop.goto(`${base}/?page=playground`);
await step("laptop width: details start closed and open on request", async () => {
  await laptop.getByLabel("Message", { exact: true }).waitFor();
  assert.equal(await laptop.getByRole("heading", { name: "Details", level: 2 }).count(), 0);
  const closed = (await laptop.locator(".pg__chat").boundingBox()).width;
  assert.ok(closed > 650, `chat width ${closed}`);
  await laptop.getByLabel("Message", { exact: true }).fill("Hello from a laptop");
  await laptop.getByRole("button", { name: "Send", exact: true }).click();
  await laptop.locator(".pg-msg__body", { hasText: "Mock reply to: Hello from a laptop" }).waitFor();
  await laptop.screenshot({ path: "shots/pg-laptop-conversation.png" });
  await laptop.getByRole("button", { name: "Show details" }).click();
  await laptop.getByRole("heading", { name: "Details", level: 2 }).waitFor();
  await laptop.screenshot({ path: "shots/pg-laptop-with-details.png" });
});

const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
watch(mobile);
await mobile.goto(`${base}/?page=playground`);
await step("mobile: conversation first, one section at a time", async () => {
  await mobile.getByRole("heading", { name: "Playground", level: 1 }).waitFor();
  await mobile.getByLabel("Message", { exact: true }).waitFor();
  assert.equal(await mobile.getByRole("button", { name: "Reset parameters" }).isVisible(), false);
  await mobile.screenshot({ path: "shots/pg-mobile-chat.png", fullPage: true });
  await mobile.getByRole("button", { name: "Settings", exact: true }).click();
  await mobile.getByRole("button", { name: "Reset parameters" }).waitFor();
  assert.equal(await mobile.getByLabel("Message", { exact: true }).isVisible(), false);
  await mobile.screenshot({ path: "shots/pg-mobile-settings.png", fullPage: true });
  await mobile.getByRole("button", { name: "Details", exact: true }).click();
  await mobile.getByText("Send a message to see the exact request body.").waitFor();
  const overflow = await mobile.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  assert.ok(overflow <= 0, `horizontal overflow ${overflow}px`);
});

await step("mobile: a long unbroken word and the code view do not widen the page", async () => {
  await mobile.getByRole("button", { name: "Conversation", exact: true }).click();
  const longWord = "x".repeat(260);
  await mobile.getByLabel("Message", { exact: true }).fill(longWord);
  await mobile.getByRole("button", { name: "Send", exact: true }).click();
  await mobile.locator(".pg-msg__body", { hasText: "Mock reply to:" }).waitFor();
  const chatOverflow = await mobile.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  assert.ok(chatOverflow <= 0, `chat overflow ${chatOverflow}px`);
  await mobile.screenshot({ path: "shots/pg-mobile-conversation.png", fullPage: true });
  await mobile.getByRole("button", { name: "Details", exact: true }).click();
  await mobile.getByRole("button", { name: "Code", exact: true }).click();
  await mobile.locator(".pg-details pre").waitFor();
  const codeOverflow = await mobile.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  assert.ok(codeOverflow <= 0, `code overflow ${codeOverflow}px`);
  await mobile.screenshot({ path: "shots/pg-mobile-code.png", fullPage: true });
});

await step("no console errors or warnings in the whole session", async () => {
  const unexpected = consoleProblems.filter((m) => !m.includes("status of 503"));
  assert.deepEqual(unexpected, []);
});

await browser.close();
server.close();
const failed = results.filter(([s]) => s === "FAIL").length;
console.log(`\n${results.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
