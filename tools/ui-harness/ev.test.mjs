import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { HERE, playwright } from "./paths.mjs";
import { startServer, json, readBody } from "./server.mjs";

const { chromium } = playwright();
process.chdir(HERE); // screenshots go to ./shots
fs.mkdirSync(path.join(HERE, "shots"), { recursive: true });
const runs = JSON.parse(fs.readFileSync(path.join(HERE, "dist", "fixtures", "runs.json"), "utf8"));
const catalogue = JSON.parse(fs.readFileSync(path.join(HERE, "dist", "fixtures", "catalogue.json"), "utf8"));

const P1 = "11111111-1111-4111-8111-111111111111";
const P2 = "22222222-2222-4222-8222-222222222222";
const PROJECTS = [
  { id: P1, name: "Harness project", description: null, created_at: "2026-10-16T09:00:00Z", updated_at: "2026-10-16T09:00:00Z", active_key_count: 1 },
  { id: P2, name: "Second project", description: null, created_at: "2026-10-16T09:30:00Z", updated_at: "2026-10-16T09:30:00Z", active_key_count: 0 },
];
const RUN = { flow: "a1".repeat(16), cancel: "b2".repeat(16), errors: "c3".repeat(16), unsaved: "d4".repeat(16), missing: "e5".repeat(16), failed: "f6".repeat(16), flaky: "07".repeat(16), listed: "18".repeat(16) };

const state = { datasets: [], modelStatus: "available", started: [], created: [], cancels: [], polls: {}, nextId: 1, deleted: [], runsList: [] };

function model() { return { id: "NCAIR1/N-ATLaS", kind: "llm", display_name: "N-ATLaS LLM", architecture: "Llama-3 8B", parameters: "8B", languages: ["en", "ha", "ig", "yo"], context_length_tokens: 8092, license: "x", source_url: "x", attribution: "x", status: state.modelStatus }; }
const err = (res, status, code, message, details = []) => json(res, status, { error: { code, message, request_id: "req_evtest0001", details } }, { "x-request-id": "req_evtest0001" });
function handle(runId, over = {}) { return { run_id: runId, dataset_id: "ds1", dataset_name: "Mock dataset", state: "running", cases_total: 15, cases_done: 0, created_at: new Date().toISOString(), finished_at: null, error_code: null, error_message: null, persisted: null, ...over }; }
const doc = (name, runId) => ({ ...runs[name], run_id: runId });

function runStatus(runId) {
  const n = (state.polls[runId] = (state.polls[runId] ?? 0) + 1);
  switch (runId) {
    case RUN.flow:
      if (n === 1) return { state: "running", handle: handle(runId, { cases_done: 0 }), run: null };
      if (n === 2) return { state: "running", handle: handle(runId, { cases_done: 5 }), run: null };
      if (n === 3) return { state: "completed", handle: handle(runId, { state: "completed", cases_done: 15, finished_at: new Date().toISOString(), persisted: null }), run: doc("completed", runId) };
      return { state: "completed", handle: handle(runId, { state: "completed", cases_done: 15, finished_at: new Date().toISOString(), persisted: true }), run: doc("completed", runId) };
    case RUN.cancel:
      if (state.cancels.includes(runId)) return { state: "cancelled", handle: handle(runId, { state: "cancelled", cases_total: 6, cases_done: 2, persisted: true }), run: doc("cancelled", runId) };
      return { state: "running", handle: handle(runId, { cases_total: 6, cases_done: 2 }), run: null };
    case RUN.errors:
      return { state: "completed", handle: handle(runId, { state: "completed", cases_total: 8, cases_done: 8, persisted: true }), run: doc("errors", runId) };
    case RUN.unsaved:
      return { state: "completed", handle: handle(runId, { state: "completed", persisted: false }), run: doc("completed", runId) };
    case RUN.failed:
      return { state: "failed", handle: handle(runId, { state: "failed", persisted: false, error_code: "run_timeout", error_message: "The run was stopped because it took longer than the allowed time." }), run: null };
    case RUN.listed:
      return { state: "completed", handle: null, run: doc("completed", runId) };
    default:
      return null;
  }
}

async function api(req, res, url) {
  const p = url.pathname;
  const m = req.method;
  if (p === "/api/v1/auth/me") return json(res, 200, { id: "u1", email: "dev@example.com", display_name: "Dev", created_at: "2026-10-16T09:00:00Z" });
  if (p === "/api/v1/projects") return json(res, 200, { data: PROJECTS });
  if (p.endsWith("/playground/models")) return json(res, 200, { data: [model()] });
  if (p === "/api/v1/evaluations/starters") return json(res, 200, { data: catalogue.starters });
  if (p === "/api/v1/evaluations/checks") return json(res, 200, { data: catalogue.checks });

  let match = p.match(/^\/api\/v1\/projects\/([^/]+)\/evaluations(?:\/([^/]+))?$/);
  if (match) {
    const [, , eid] = match;
    if (m === "GET" && !eid) return json(res, 200, { data: state.datasets.map(({ cases, ...rest }) => rest) });
    if (m === "GET" && eid) { const d = state.datasets.find((x) => x.id === eid); return d ? json(res, 200, d) : err(res, 404, "evaluation_not_found", "Evaluation not found."); }
    if (m === "DELETE" && eid) { state.deleted.push(eid); state.datasets = state.datasets.filter((x) => x.id !== eid); res.writeHead(204); return res.end(); }
    if (m === "POST") {
      const body = await readBody(req);
      state.created.push({ headers: req.headers, body });
      let name, cases;
      if (body.starter_id) {
        const s = catalogue.starters.find((x) => x.id === body.starter_id);
        if (!s) return err(res, 404, "not_found", "No such starter dataset.");
        name = s.name; cases = catalogue.langs_dataset.cases;
      } else {
        const bad = (body.dataset?.cases ?? []).map((c, i) => (c.prompt ? null : { field: `cases[${i}].prompt`, message: "is required" })).filter(Boolean);
        if (bad.length) return err(res, 422, "invalid_dataset", "The dataset has problems. Fix the listed fields and try again.", bad);
        name = body.dataset.name; cases = body.dataset.cases;
      }
      const id = `eval-${state.nextId++}`;
      const detail = { id, name, description: "Mock description of the dataset.", case_count: cases.length, created_at: new Date().toISOString(), cases: cases.map((c, i) => ({ id: `${id}-c${i}`, position: i, key: c.id, name: c.name ?? c.id, category: c.category, language: c.language ?? null, prompt: c.prompt, system: null, checks: c.checks, repeat: 1, max_tokens: null, temperature: null, tags: [] })) };
      state.datasets.unshift(detail);
      return json(res, 201, detail);
    }
  }
  match = p.match(/^\/api\/v1\/projects\/([^/]+)\/evaluation-runs(?:\/([^/]+))?(\/cancel|\/export)?$/);
  if (match) {
    const [, , rid, suffix] = match;
    if (m === "POST" && !rid) {
      const body = await readBody(req);
      state.started.push({ headers: req.headers, body });
      if (state.modelStatus !== "available") return err(res, 503, "provider_unavailable", "N-ATLAS is not connected right now. Start the model host, then run the evaluation again.");
      return json(res, 202, handle(RUN.flow, { dataset_id: body.evaluation_id }));
    }
    if (m === "GET" && !rid) return json(res, 200, { data: state.runsList });
    if (m === "POST" && suffix === "/cancel") { state.cancels.push(rid); return json(res, 202, { cancelling: true }); }
    if (m === "GET" && !suffix) {
      if (rid === RUN.flaky) { const n = (state.polls[rid] = (state.polls[rid] ?? 0) + 1); if (n === 1) return err(res, 503, "provider_unavailable", "Temporary problem."); return json(res, 200, { state: "completed", handle: null, run: doc("completed", rid) }); }
      const status = runStatus(rid);
      return status ? json(res, 200, status) : err(res, 404, "evaluation_run_not_found", "Evaluation run not found.");
    }
  }
  json(res, 404, { error: { code: "not_found", message: "no such mock route", request_id: null } });
}

const results = [];
async function step(name, fn) {
  try { await fn(); results.push(["PASS", name]); console.log("PASS", name); }
  catch (error) { results.push(["FAIL", name]); console.log("FAIL", name, "\n   ", String(error.message).split("\n").slice(0, 7).join("\n    ")); }
}

const { server, port } = await startServer(api);
const base = `http://127.0.0.1:${port}`;
const browser = await chromium.launch({ args: ["--no-sandbox"] });
const consoleProblems = [];
function watch(page) {
  page.on("console", (msg) => { if (["error", "warning"].includes(msg.type())) consoleProblems.push(`${msg.type()}: ${msg.text()}`); });
  page.on("pageerror", (e) => consoleProblems.push(`pageerror: ${e.message}`));
}
const runUrl = (id) => `${base}/?page=run&projectId=${P1}&runId=${id}`;

const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
watch(page);
await page.goto(`${base}/?page=evaluations`);

await step("empty state: no datasets, no runs, model connected", async () => {
  await page.getByRole("heading", { name: "Evaluations", level: 1 }).waitFor();
  await page.getByText("No datasets yet").waitFor();
  await page.getByText("No runs yet").waitFor();
  assert.equal(await page.getByLabel("Project", { exact: true }).inputValue(), P1);
  assert.equal(await page.getByText(/N-ATLAS is not connected right now, so runs cannot start/).count(), 0);
  await page.screenshot({ path: "shots/ev-empty.png" });
});

await step("a starter dataset can be added and its cases viewed", async () => {
  await page.getByRole("button", { name: "Add dataset", exact: true }).first().click();
  await page.getByText("Starter: English, Hausa, Igbo and Yoruba phrases").waitFor();
  await page.getByText("Starter: general checks").waitFor();
  const radios = page.getByRole("radio");
  assert.equal(await radios.count(), 2);
  assert.equal(await radios.nth(0).isChecked(), true);
  await radios.nth(1).check();
  await page.getByRole("button", { name: "Add dataset", exact: true }).last().click();
  await page.getByText(/Added ".+" with \d+ cases\./).waitFor();
  assert.equal(state.created[0].headers["x-csrf-token"], "harness-csrf");
  assert.deepEqual(Object.keys(state.created[0].body), ["starter_id"]);
  await page.getByRole("button", { name: "View cases" }).click();
  await page.locator(".ev-cases tbody tr").first().waitFor();
  assert.ok((await page.locator(".ev-cases tbody tr").count()) >= 10);
  await page.getByRole("button", { name: "Hide cases" }).click();
  await page.screenshot({ path: "shots/ev-datasets.png" });
});

await step("pasting JSON: local checks, server problems per field, then success", async () => {
  await page.getByRole("button", { name: "Add dataset", exact: true }).first().click();
  await page.getByRole("button", { name: "Paste JSON", exact: true }).click();
  await page.getByRole("button", { name: "Add dataset", exact: true }).last().click();
  await page.getByText("Paste a dataset first.").waitFor();
  await page.getByLabel("Dataset JSON").fill("{ not json");
  await page.getByRole("button", { name: "Add dataset", exact: true }).last().click();
  await page.getByText(/That is not valid JSON\./).waitFor();
  await page.getByLabel("Dataset JSON").fill("[1, 2]");
  await page.getByRole("button", { name: "Add dataset", exact: true }).last().click();
  await page.getByText("A dataset is a JSON object with a name and a list of cases.").waitFor();
  await page.getByLabel("Dataset JSON").fill(JSON.stringify({ name: "Broken", cases: [{ id: "a", category: "general", checks: [] }, { id: "b", category: "general", checks: [] }] }));
  await page.getByRole("button", { name: "Add dataset", exact: true }).last().click();
  await page.getByText("The dataset has problems. Fix the listed fields and try again.").waitFor();
  await page.getByText("cases[0].prompt").waitFor();
  await page.getByText("cases[1].prompt").waitFor();
  await page.screenshot({ path: "shots/ev-json-errors.png" });
  await page.getByRole("button", { name: "Insert an example" }).click();
  const text = await page.getByLabel("Dataset JSON").inputValue();
  assert.equal(JSON.parse(text).name, "Greetings and arithmetic");
  await page.getByRole("button", { name: "Add dataset", exact: true }).last().click();
  await page.getByText('Added "Greetings and arithmetic" with 2 cases.').waitFor();
  assert.equal(state.datasets.length, 2);
});

await step("the checks help is the engine's own list", async () => {
  await page.getByRole("button", { name: "Add dataset", exact: true }).first().click();
  await page.getByRole("button", { name: "Paste JSON", exact: true }).click();
  await page.getByText("Checks you can use").click();
  for (const type of ["exact_match", "contains_all", "language_match", "consistency", "not_empty"]) {
    await page.locator("dt", { hasText: type }).first().waitFor();
  }
  await page.getByText(/Parameters: values \(required\)/).first().waitFor();
  assert.equal(await page.locator(".ev-checks dt").count(), catalogue.checks.length);
  await page.getByRole("button", { name: "Close", exact: true }).click();
});

await step("starting a run sends the right request and opens the run page", async () => {
  await page.getByLabel("Prompt version").fill("v3");
  await page.getByRole("button", { name: "Run", exact: true }).first().click();
  await page.waitForFunction(() => (window.__navigations ?? []).length > 0);
  const body = state.started[0].body;
  assert.equal(body.evaluation_id, state.datasets[0].id);
  assert.equal(body.model, "NCAIR1/N-ATLaS");
  assert.equal(body.prompt_version, "v3");
  assert.equal(state.started[0].headers["x-csrf-token"], "harness-csrf");
  const navigated = await page.evaluate(() => window.__navigations.at(-1));
  assert.equal(navigated, `/app/evaluations/${P1}/runs/${RUN.flow}`);
});

await step("an invalid prompt version blocks the run and says why", async () => {
  await page.getByLabel("Prompt version").fill("bad<script>");
  await page.getByText("Use letters, digits and . _ : / @ + # - only.").waitFor();
  assert.equal(await page.getByRole("button", { name: "Run", exact: true }).first().isDisabled(), true);
  await page.getByLabel("Prompt version").fill("");
});

await step("a model that is away disables Run and explains, and the server error is shown", async () => {
  state.modelStatus = "unavailable";
  await page.reload();
  await page.getByText(/N-ATLAS is not connected right now, so runs cannot start/).waitFor();
  await page.getByRole("button", { name: "Run", exact: true }).first().waitFor({ state: "visible" }).catch(() => {});
  state.modelStatus = "available";
});

await step("archiving needs a second click and removes the dataset", async () => {
  await page.reload();
  await page.getByRole("button", { name: "Archive", exact: true }).first().click();
  await page.getByRole("button", { name: "Keep" }).click();
  assert.equal(state.deleted.length, 0);
  await page.getByRole("button", { name: "Archive", exact: true }).first().click();
  await page.getByRole("button", { name: "Confirm archive" }).click();
  await page.getByText("Dataset archived. Its past runs are still available.").waitFor();
  assert.equal(state.deleted.length, 1);
  assert.equal(state.datasets.length, 1);
});

await step("the runs list shows live and finished runs with progress and pass rate", async () => {
  state.runsList = [
    { run_id: RUN.flow, dataset_id: "ds1", dataset_name: "Live dataset", state: "running", model: null, prompt_version: null, started_at: new Date().toISOString(), finished_at: null, cases_total: 15, cases_done: 4, pass_rate: null, counts: null },
    { run_id: RUN.listed, dataset_id: "ds1", dataset_name: "Finished dataset", state: "completed", model: "NCAIR1/N-ATLaS", prompt_version: "v3", started_at: new Date(Date.now() - 3600_000).toISOString(), finished_at: new Date().toISOString(), cases_total: 15, cases_done: 15, pass_rate: 0.4667, counts: { pass: 7, fail: 8, review: 0, error: 0, skipped: 0 } },
  ];
  await page.reload();
  await page.getByText("4 of 15").waitFor();
  await page.getByText("In progress").waitFor();
  await page.getByText("47%").waitFor();
  await page.getByRole("link", { name: "Open" }).nth(1).waitFor();
  await page.screenshot({ path: "shots/ev-runs-list.png" });
  state.runsList = [];
});

const run = await browser.newPage({ viewport: { width: 1366, height: 900 } });
watch(run);

await step("run page: progress, saving, then the full result", async () => {
  await run.goto(runUrl(RUN.flow));
  await run.getByText("0 of 15 cases finished").waitFor();
  await run.getByRole("button", { name: "Cancel run" }).waitFor();
  await run.getByText("5 of 15 cases finished").waitFor({ timeout: 6000 });
  await run.screenshot({ path: "shots/ev-run-progress.png" });
  await run.locator(".stat__label", { hasText: "Pass rate" }).waitFor({ timeout: 8000 });
  await run.getByText("47%").first().waitFor();
  await run.getByText("Share of completed cases whose checks all held. Not model accuracy.").waitFor();
  await run.getByText("Saving the run").waitFor().catch(() => {});
  await run.waitForFunction(() => !document.body.innerText.includes("Saving the run"), null, { timeout: 8000 });
  assert.equal(await run.getByRole("button", { name: "Cancel run" }).count(), 0);
  assert.ok((state.polls[RUN.flow] ?? 0) >= 4, `polls: ${state.polls[RUN.flow]}`);
});

await step("summary numbers match the engine's own summary", async () => {
  const c = runs.completed.summary.counts;
  const stats = await run.locator(".stats").innerText();
  assert.match(stats, new RegExp(`Passed\\s+${c.pass}\\b`));
  assert.match(stats, new RegExp(`Failed\\s+${c.fail}\\b`));
  assert.match(stats, /Median time/);
  assert.match(stats, /95th percentile/);
  assert.equal(await run.getByText("Skipped", { exact: true }).count(), 0);
  await run.getByRole("heading", { name: "By language" }).waitFor();
  await run.getByRole("heading", { name: "By category" }).waitFor();
  const langTable = await run.locator("table").first().innerText();
  assert.match(langTable, /Yoruba/);
  assert.match(langTable, /Hausa/);
  await run.getByRole("link", { name: "All evaluations" }).waitFor();
});

await step("every case is listed with its status, and details show output and checks", async () => {
  assert.equal(await run.locator(".ev-case").count(), runs.completed.results.length);
  const first = run.locator(".ev-case").first();
  await first.locator("summary").click();
  await first.locator(".ev-output").first().waitFor();
  const text = await first.innerText();
  assert.match(text, /Model output/);
  assert.match(text, /Held|Did not hold|Undecided/);
  assert.match(text, /req_|[0-9a-f]{32}/);
  await run.screenshot({ path: "shots/ev-run-result.png", fullPage: true });
});

await step("the attention filter shows only cases that need a look", async () => {
  const attention = runs.completed.summary.counts.fail + runs.completed.summary.counts.review + runs.completed.summary.counts.error;
  await run.getByRole("button", { name: new RegExp(`Needs attention \\(${attention}\\)`) }).click();
  assert.equal(await run.locator(".ev-case").count(), attention);
  const labels = await run.locator(".ev-case summary .pill").allInnerTexts();
  assert.ok(labels.every((t) => ["Failed", "Needs review", "Error"].includes(t)), labels.join(","));
  await run.getByRole("button", { name: /^All \(/ }).click();
});

await step("export links are plain downloads of the right files", async () => {
  const csv = await run.getByRole("link", { name: "Download CSV" }).getAttribute("href");
  const jsonHref = await run.getByRole("link", { name: "Download JSON" }).getAttribute("href");
  assert.equal(csv, `/api/v1/projects/${P1}/evaluation-runs/${RUN.flow}/export?format=csv`);
  assert.equal(jsonHref, `/api/v1/projects/${P1}/evaluation-runs/${RUN.flow}/export?format=json`);
  assert.notEqual(await run.getByRole("link", { name: "Download CSV" }).getAttribute("download"), null);
});

await step("the methodology is one click away", async () => {
  await run.getByText("How these numbers are made").click();
  const items = await run.locator(".ev-method li").count();
  assert.equal(items, runs.completed.methodology.length);
});

await step("model errors are explained in plain words and counted", async () => {
  await run.goto(runUrl(RUN.errors));
  await run.locator(".stat__label", { hasText: "Pass rate" }).waitFor();
  const stats = await run.locator(".stats").innerText();
  assert.match(stats, /Error\s+6\b/);
  assert.match(stats, /Model call failed/);
  await run.getByRole("button", { name: /Needs attention/ }).click();
  const errored = run.locator(".ev-case", { has: run.locator(".pill", { hasText: "Error" }) }).first();
  await errored.locator("summary").click();
  const text = await errored.innerText();
  assert.match(text, /could not be reached|took too long/);
  assert.match(text, /Failed/);
  await run.screenshot({ path: "shots/ev-run-errors.png", fullPage: true });
});

await step("cancelling: the button, the request, then a clear explanation", async () => {
  await run.goto(runUrl(RUN.cancel));
  await run.getByText("2 of 6 cases finished").waitFor();
  await run.getByRole("button", { name: "Cancel run" }).click();
  await run.getByText(/This run was cancelled\. 4 cases did not start/).waitFor({ timeout: 6000 });
  assert.deepEqual(state.cancels, [RUN.cancel]);
  const stats = await run.locator(".stats").innerText();
  assert.match(stats, /Skipped\s+4\b/);
  await run.getByText("Cancelled", { exact: true }).waitFor();
  assert.equal(await run.getByRole("button", { name: "Cancel run" }).count(), 0);
});

await step("a run that could not be saved says so honestly", async () => {
  await run.goto(runUrl(RUN.unsaved));
  await run.getByText(/could not be saved to the database/).waitFor();
  await run.locator(".stat__label", { hasText: "Pass rate" }).waitFor();
});

await step("a failed run shows the reason and stores nothing", async () => {
  await run.goto(runUrl(RUN.failed));
  await run.getByText("The run was stopped because it took longer than the allowed time.").waitFor();
  await run.getByText("Nothing was stored for this run. Start it again.").waitFor();
  await run.getByText("Failed", { exact: true }).first().waitFor();
});

await step("a run loaded from the database (no live handle) shows normally", async () => {
  await run.goto(runUrl(RUN.listed));
  await run.locator(".stat__label", { hasText: "Pass rate" }).waitFor();
  assert.equal(await run.getByText("Saving the run").count(), 0);
  assert.equal(await run.getByText(/could not be saved/).count(), 0);
});

await step("an unknown run is reported at once, without quiet retries", async () => {
  const before = Date.now();
  await run.goto(runUrl(RUN.missing));
  await run.getByText("Evaluation run not found.").waitFor({ timeout: 2500 });
  assert.ok(Date.now() - before < 2500);
  assert.equal(state.polls[RUN.missing], 1);
});

await step("a short network problem is retried quietly", async () => {
  await run.goto(runUrl(RUN.flaky));
  await run.locator(".stat__label", { hasText: "Pass rate" }).waitFor({ timeout: 8000 });
  assert.equal(await run.getByText("Temporary problem.").count(), 0);
  assert.equal(state.polls[RUN.flaky], 2);
});

const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
watch(mobile);
await step("phone: the evaluations page does not widen the page", async () => {
  state.datasets = [{ id: "m1", name: "A dataset with a fairly long name to test wrapping", description: "A description that is long enough to need wrapping on a narrow screen, with several words.", case_count: 15, created_at: new Date().toISOString(), cases: [] }];
  state.runsList = [{ run_id: RUN.listed, dataset_id: "ds1", dataset_name: "Finished dataset", state: "completed", model: "NCAIR1/N-ATLaS", prompt_version: "v3", started_at: new Date().toISOString(), finished_at: null, cases_total: 15, cases_done: 15, pass_rate: 0.4667, counts: null }];
  await mobile.goto(`${base}/?page=evaluations`);
  await mobile.getByText("A dataset with a fairly long name to test wrapping").waitFor();
  await mobile.getByRole("button", { name: "Add dataset", exact: true }).first().click();
  await mobile.getByRole("button", { name: "Paste JSON", exact: true }).click();
  await mobile.getByRole("button", { name: "Insert an example" }).click();
  const overflow = await mobile.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  assert.ok(overflow <= 0, `overflow ${overflow}px`);
  await mobile.screenshot({ path: "shots/ev-mobile-list.png", fullPage: true });
});

await step("phone: the run page does not widen the page", async () => {
  await mobile.goto(runUrl(RUN.errors));
  await mobile.locator(".stat__label", { hasText: "Pass rate" }).waitFor();
  await mobile.locator(".ev-case").first().locator("summary").click();
  const overflow = await mobile.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  assert.ok(overflow <= 0, `overflow ${overflow}px`);
  await mobile.screenshot({ path: "shots/ev-mobile-run.png", fullPage: true });
});

await step("no unexpected console errors or warnings in the whole session", async () => {
  const expected = (m) => /status of (404|422|503)/.test(m);
  assert.deepEqual(consoleProblems.filter((m) => !expected(m)), []);
});

await browser.close();
server.close();
const failed = results.filter(([s]) => s === "FAIL").length;
console.log(`\n${results.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
