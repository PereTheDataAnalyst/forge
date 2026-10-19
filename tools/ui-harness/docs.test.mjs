// Documentation page checks: the contents list and the section that covers the two web app tools.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { HERE, playwright } from "./paths.mjs";
import { startServer, json } from "./server.mjs";

const { chromium } = playwright();
process.chdir(HERE);
fs.mkdirSync(path.join(HERE, "shots"), { recursive: true });

function api(req, res, url) {
  if (url.pathname === "/api/v1/auth/me") {
    return json(res, 401, { error: { code: "not_authenticated", message: "Sign in to continue.", request_id: null } });
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
const consoleProblems = [];
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on("console", (m) => {
  if (!["error", "warning"].includes(m.type()) || /status of 401/.test(m.text())) return;
  consoleProblems.push(`${m.type()}: ${m.text()}`);
});
page.on("pageerror", (e) => consoleProblems.push(`pageerror: ${e.message}`));
await page.goto(`${base}/?page=docs&layout=public`);
await page.locator("#quick-start").waitFor();

await step("the contents list names every section, in page order", async () => {
  const links = await page.locator("main a[href^='#']").evaluateAll((els) => els.map((a) => a.getAttribute("href")));
  const expected = ["#quick-start", "#authentication", "#endpoints", "#web-app", "#errors", "#rate-limits", "#connect-n-atlas", "#licensing"];
  assert.deepEqual(links.slice(0, expected.length), expected);
  const order = await page.locator("main section[id]").evaluateAll((els) => els.map((s) => s.id));
  assert.deepEqual(order, expected.map((h) => h.slice(1)));
});

await step("every contents link has exactly one section to land on", async () => {
  for (const id of ["quick-start", "authentication", "endpoints", "web-app", "errors", "rate-limits", "connect-n-atlas", "licensing"]) {
    assert.equal(await page.locator(`[id="${id}"]`).count(), 1, id);
  }
});

await step("the new section describes both tools and links to them", async () => {
  const section = page.locator("#web-app");
  const text = await section.innerText();
  for (const fact of ["Playground.", "Evaluation Lab.", "not stored", "no accuracy score", "CSV or JSON"]) assert.ok(text.includes(fact), fact);
  assert.equal(await section.getByRole("link", { name: "Playground", exact: true }).getAttribute("href"), "/app/playground");
  assert.equal(await section.getByRole("link", { name: "Evaluations", exact: true }).getAttribute("href"), "/app/evaluations");
  await section.screenshot({ path: "shots/docs-web-app.png" });
});

await step("the console stays free of errors and warnings", async () => {
  assert.deepEqual(consoleProblems, []);
});

await browser.close();
server.close();
const failed = results.filter(([r]) => r === "FAIL");
console.log(`\n${results.length - failed.length} of ${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
