// Dashboard checks: the getting-started checklist follows real data, and the Platform panel lists
// every capability with its real status and a working link.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { HERE, playwright } from "./paths.mjs";
import { startServer, json } from "./server.mjs";

const { chromium } = playwright();
process.chdir(HERE);
fs.mkdirSync(path.join(HERE, "shots"), { recursive: true });

const USER = { id: "u1", email: "dev@example.com", display_name: "Dev", created_at: "2026-10-15T09:00:00Z" };
const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const iso = (minutesAgo) => new Date(Date.now() - minutesAgo * 60000).toISOString();
const REQUESTS = [1, 2, 3].map((n) => ({
  id: `r${n}`, request_id: `req_mock000${n}`, created_at: iso(n * 5), project_id: PROJECT_ID, project_name: "Harness project",
  endpoint: "/api/v1/chat", method: "POST", status_code: 200, outcome: "success", error_code: null, latency_ms: 400 + n,
  model: "NCAIR1/N-ATLaS", language: "en", prompt_tokens: 12, completion_tokens: 8,
}));
const KEY = { id: "k1", project_id: PROJECT_ID, project_name: "Harness project", name: "Server key", prefix: "fk_live_abcd", scopes: ["chat.create"], created_at: iso(120), last_used_at: iso(5), expires_at: null };
const RUN = { id: "run1", project_id: PROJECT_ID, status: "completed", evaluation_name: "Starter: general checks", model: "NCAIR1/N-ATLaS", created_at: iso(30) };
const EMPTY = {
  window_days: 30,
  provider: { status: "ok", model_id: "NCAIR1/N-ATLaS", detail: null },
  totals: { projects: 0, active_api_keys: 0, requests: 0, successful: 0, failed: 0, average_latency_ms: null },
  recent_requests: [], recent_api_keys: [], recent_evaluation_runs: [],
};
const BUSY = { projects: 1, active_api_keys: 1, requests: 3, successful: 3, failed: 0, average_latency_ms: 401 };
const SUMMARIES = {
  new: EMPTY,
  projectOnly: { ...EMPTY, totals: { ...EMPTY.totals, projects: 1 } },
  partial: { ...EMPTY, totals: BUSY, recent_requests: REQUESTS, recent_api_keys: [KEY] },
  outsideWindow: { ...EMPTY, totals: { ...EMPTY.totals, projects: 1, active_api_keys: 1 }, recent_requests: REQUESTS, recent_api_keys: [KEY] },
  complete: { ...EMPTY, totals: BUSY, recent_requests: REQUESTS, recent_api_keys: [KEY], recent_evaluation_runs: [RUN] },
  modelAway: { ...EMPTY, provider: { status: "unreachable", model_id: "NCAIR1/N-ATLaS", detail: "The host did not answer." } },
};
const state = { kind: "new" };

function api(req, res, url) {
  const p = url.pathname;
  if (p === "/api/v1/auth/me") return json(res, 200, USER);
  if (p.endsWith("/dashboard/summary")) {
    if (state.kind === "error") return json(res, 500, { error: { code: "internal_error", message: "Something went wrong on our side.", request_id: "req_dashfail" } });
    return json(res, 200, SUMMARIES[state.kind]);
  }
  json(res, 404, { error: { code: "not_found", message: "no such mock route", request_id: null } });
}

const results = [];
async function step(name, fn) {
  try { await fn(); results.push(["PASS", name]); console.log("PASS", name); }
  catch (error) { results.push(["FAIL", name]); console.log("FAIL", name, "\n   ", String(error.message).split("\n").slice(0, 8).join("\n    ")); }
}

const { server, port } = await startServer(api);
const base = `http://127.0.0.1:${port}`;
const browser = await chromium.launch({ args: ["--no-sandbox"] });
const PROVOKED = /status of 500/;
const consoleProblems = [];
function watch(page) {
  page.on("console", (m) => {
    if (!["error", "warning"].includes(m.type()) || PROVOKED.test(m.text())) return;
    consoleProblems.push(`${m.type()}: ${m.text()}`);
  });
  page.on("pageerror", (e) => consoleProblems.push(`pageerror: ${e.message}`));
}

async function visit(kind, fn, viewport = { width: 1440, height: 900 }) {
  state.kind = kind;
  const page = await browser.newPage({ viewport });
  watch(page);
  try {
    await page.goto(`${base}/app?page=dashboard`);
    await page.getByRole("heading", { level: 1, name: "Dashboard" }).waitFor();
    if (kind === "error") await page.locator(".notice__message").waitFor();
    else await page.getByRole("heading", { level: 2, name: "N-ATLAS inference" }).waitFor();
    await fn(page);
  } finally {
    await page.close();
  }
}

const checklist = (page) => page.locator("section.panel", { has: page.getByRole("heading", { level: 2, name: "Get started" }) });
const platform = (page) => page.locator("section.panel", { has: page.getByRole("heading", { level: 2, name: "Platform", exact: true }) });
const hrefs = (locator) => locator.evaluateAll((els) => els.map((a) => a.getAttribute("href")));

await step("a new account sees every step open, each with a link to where it is done", () =>
  visit("new", async (page) => {
    const panel = checklist(page);
    assert.match(await panel.innerText(), /0 of 4 done/);
    assert.equal(await panel.locator(".checklist__item").count(), 4);
    assert.equal(await panel.locator(".checklist__item--done").count(), 0);
    assert.deepEqual(await panel.getByRole("link").allTextContents(), ["Open projects", "Open projects", "Open the Playground", "Open Evaluations"]);
    assert.deepEqual(await hrefs(panel.getByRole("link")), ["/app/projects", "/app/projects", "/app/playground", "/app/evaluations"]);
    assert.match(await panel.locator(".checklist__item").first().innerText(), /To do:\s*Create a project/);
    await page.screenshot({ path: "shots/dash-desktop-new.png", fullPage: true });
  }));

await step("a project alone completes exactly the first step", () =>
  visit("projectOnly", async (page) => {
    const panel = checklist(page);
    assert.match(await panel.innerText(), /1 of 4 done/);
    assert.match(await panel.locator(".checklist__item--done").innerText(), /Done:\s*Create a project/);
    assert.equal(await panel.getByRole("link").count(), 3, "a finished step has no link");
  }));

await step("progress follows real data: three steps done leaves only the evaluation open", () =>
  visit("partial", async (page) => {
    const panel = checklist(page);
    assert.match(await panel.innerText(), /3 of 4 done/);
    assert.deepEqual(await hrefs(panel.getByRole("link")), ["/app/evaluations"]);
    await page.screenshot({ path: "shots/dash-desktop-partial.png", fullPage: true });
  }));

await step("a request outside the 30 day count still completes the step when it is among the latest", () =>
  visit("outsideWindow", async (page) => {
    assert.match(await checklist(page).innerText(), /3 of 4 done/);
  }));

await step("when everything is done the checklist is gone and the dashboard is about activity", () =>
  visit("complete", async (page) => {
    assert.equal(await page.getByRole("heading", { level: 2, name: "Get started" }).count(), 0);
    await page.getByRole("heading", { level: 2, name: "Recent requests" }).waitFor();
    await page.getByRole("heading", { level: 2, name: "Platform", exact: true }).waitFor();
  }));

await step("the Platform panel lists every capability with its real status", () =>
  visit("new", async (page) => {
    const panel = platform(page);
    assert.match(await panel.innerText(), /6 available, 4 in development/);
    assert.deepEqual(await panel.locator(".features__name").allTextContents(), [
      "API gateway", "Projects and API keys", "Playground", "Evaluation Lab", "Observability", "Documentation",
      "Request logs and charts", "SDKs", "Voice", "MCP",
    ]);
    const pills = await panel.locator(".features__item .pill").allTextContents();
    assert.equal(pills.filter((t) => t.trim() === "Available").length, 6);
    assert.equal(pills.filter((t) => t.trim() === "In development").length, 4);
    const mcp = panel.locator(".features__item", { hasText: "MCP" });
    assert.match(await mcp.innerText(), /In development/);
    assert.equal(await mcp.getByRole("link").count(), 0, "an unbuilt capability is not linked");
  }));

await step("available capabilities link to where they live", () =>
  visit("new", async (page) => {
    const links = platform(page).getByRole("link");
    assert.deepEqual(await links.evaluateAll((els) => els.map((a) => a.getAttribute("aria-label"))), [
      "Read API gateway", "Open Projects and API keys", "Open Playground", "Open Evaluation Lab", "Read Documentation",
    ]);
    assert.deepEqual(await hrefs(links), ["/docs#endpoints", "/app/projects", "/app/playground", "/app/evaluations", "/docs"]);
    await page.getByRole("link", { name: "Open Playground", exact: true }).click();
    assert.deepEqual(await page.evaluate(() => window.__navigations), ["/app/playground"]);
  }));

await step("panels run in order: provider, get started, platform, then activity", () =>
  visit("new", async (page) => {
    assert.deepEqual(await page.locator("h2.panel__title").allTextContents(), [
      "N-ATLAS inference", "Get started", "Platform", "Recent requests", "Active API keys", "Recent evaluation runs",
    ]);
  }));

await step("a missing model still shows the platform overview", () =>
  visit("modelAway", async (page) => {
    await page.getByText("Unreachable", { exact: true }).waitFor();
    await page.getByRole("heading", { level: 2, name: "Platform", exact: true }).waitFor();
  }));

await step("an API error shows the notice and no half-built panels", () =>
  visit("error", async (page) => {
    assert.match(await page.locator(".notice__message").innerText(), /Something went wrong on our side/);
    assert.equal(await page.getByRole("heading", { level: 2, name: "Get started" }).count(), 0);
  }));

await step("phone layout: no sideways scroll, and the actions stay reachable", () =>
  visit("new", async (page) => {
    const report = await page.evaluate(() => {
      const iw = window.innerWidth;
      const wide = [...document.querySelectorAll("body *")]
        .filter((el) => el.getBoundingClientRect().right > iw + 1 && el.getBoundingClientRect().width > 0)
        .slice(0, 6).map((el) => `${el.tagName.toLowerCase()}.${String(el.className).slice(0, 40)} right=${Math.round(el.getBoundingClientRect().right)}`);
      return { sw: document.documentElement.scrollWidth, iw, wide };
    });
    assert.ok(report.sw <= report.iw, `page is ${report.sw}px wide in ${report.iw}px: ${report.wide.join("; ")}`);
    for (const link of await checklist(page).getByRole("link").all()) {
      const box = await link.boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= report.iw, "action inside the screen");
    }
    await page.screenshot({ path: "shots/dash-mobile-new.png", fullPage: true });
  }, { width: 390, height: 844 }));

await step("the console stays free of errors and warnings", async () => {
  assert.deepEqual(consoleProblems, []);
});

await browser.close();
server.close();
const failed = results.filter(([r]) => r === "FAIL");
console.log(`\n${results.length - failed.length} of ${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
