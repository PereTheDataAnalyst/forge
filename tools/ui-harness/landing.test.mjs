// Landing page checks: the product positioning, the live status in every state, the calls to
// action for signed-out and signed-in visitors, the capability tables, and the phone layout.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { HERE, playwright } from "./paths.mjs";
import { startServer, json } from "./server.mjs";

const { chromium } = playwright();
process.chdir(HERE); // screenshots go to ./shots
fs.mkdirSync(path.join(HERE, "shots"), { recursive: true });

const USER = { id: "u1", email: "dev@example.com", display_name: "Dev", created_at: "2026-10-15T09:00:00Z" };
const ready = (provider) => ({
  status: provider === "ok" ? "ok" : "degraded",
  checks: { database: { status: "ok" }, redis: { status: "ok" }, provider: { status: provider, model_id: "NCAIR1/N-ATLaS" } },
});
const state = { signedIn: false, ready: "ok", readyCalls: 0 };

function api(req, res, url) {
  const p = url.pathname;
  if (p === "/api/v1/auth/me") {
    return state.signedIn
      ? json(res, 200, USER)
      : json(res, 401, { error: { code: "not_authenticated", message: "Sign in to continue.", request_id: null } });
  }
  if (p === "/api/v1/health/ready") {
    state.readyCalls += 1;
    if (state.ready === "ok" || state.ready === "unreachable") return json(res, 200, ready(state.ready));
    if (state.ready === "down") {
      return json(res, 503, { status: "unavailable", checks: { database: { status: "error" }, redis: { status: "ok" }, provider: { status: "ok" } } });
    }
    if (state.ready === "dropped") return req.socket.destroy();
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

// The browser logs a failed load as a console error. Only the statuses this file provokes are allowed.
const PROVOKED = /status of (401|503)|net::ERR_/;
const consoleProblems = [];
function watch(page) {
  page.on("console", (m) => {
    if (!["error", "warning"].includes(m.type()) || PROVOKED.test(m.text())) return;
    consoleProblems.push(`${m.type()}: ${m.text()}`);
  });
  page.on("pageerror", (e) => consoleProblems.push(`pageerror: ${e.message}`));
}

async function visit(scenario, fn, viewport = { width: 1280, height: 900 }) {
  Object.assign(state, { signedIn: false, ready: "ok" }, scenario);
  state.readyCalls = 0;
  const page = await browser.newPage({ viewport });
  watch(page);
  try {
    await page.goto(`${base}/?page=landing&layout=public`);
    await page.getByRole("heading", { level: 1, name: "Call N-ATLAS from your app." }).waitFor();
    await page.waitForFunction(() => !document.querySelector(".status__note")?.textContent?.includes("Checking"));
    await fn(page);
  } finally {
    await page.close();
  }
}

async function overflowReport(page) {
  return page.evaluate(() => {
    const iw = window.innerWidth;
    const wide = [...document.querySelectorAll("body *")]
      .filter((el) => el.getBoundingClientRect().right > iw + 1 && el.getBoundingClientRect().width > 0)
      .slice(0, 6)
      .map((el) => `${el.tagName.toLowerCase()}.${String(el.className).slice(0, 40)} right=${Math.round(el.getBoundingClientRect().right)}`);
    return { scrollWidth: document.documentElement.scrollWidth, innerWidth: iw, wide };
  });
}

const pill = (page, label) => page.locator(".status__item", { hasText: label }).locator(".pill");

await step("the hero offers a product to sign up for, and no longer reads as a competition entry", () =>
  visit({}, async (page) => {
    const text = await page.locator("body").innerText();
    assert.doesNotMatch(text, /challenge|competition|hackathon|submission|judging/i);
    assert.doesNotMatch(text, /This layer is in development|Explore the playground|Nigerian Atlas for Languages/);
    const hero = page.locator(".hero__copy");
    assert.match(await hero.locator(".hero__lead").innerText(), /hosted API for N-ATLAS/);
    const create = hero.getByRole("link", { name: "Create an account", exact: true });
    assert.equal(await create.getAttribute("href"), "/register");
    assert.equal(await hero.getByRole("link", { name: "Read the documentation", exact: true }).getAttribute("href"), "/docs");
    assert.doesNotMatch(text, /\u2014/, "no em dash in rendered copy");
  }));

await step("the footer and credits no longer link to the challenge", () =>
  visit({}, async (page) => {
    const hrefs = await page.locator("a").evaluateAll((els) => els.map((a) => a.getAttribute("href") ?? ""));
    assert.equal(hrefs.filter((h) => h.includes("ncair.nitda.gov.ng")).length, 0);
    assert.ok(hrefs.includes("https://huggingface.co/NCAIR1/N-ATLaS"), "model card link stays");
    assert.ok(hrefs.includes("/#early-access") && hrefs.includes("/#start"), "new anchors are linked");
  }));

await step("live status: platform and model both up", () =>
  visit({ ready: "ok" }, async (page) => {
    assert.equal((await pill(page, "Platform").textContent()).trim(), "Up");
    assert.equal((await pill(page, "N-ATLAS model").textContent()).trim(), "Connected");
    assert.match(await page.locator(".status__note").innerText(), /Both are running/);
    assert.equal(await page.locator(".status").getAttribute("role"), "status");
    assert.equal(state.readyCalls, 1, "one request per page load, no polling");
    await page.screenshot({ path: "shots/landing-desktop-hero.png" });
  }));

await step("live status: an absent model is a warning that names the error code", () =>
  visit({ ready: "unreachable" }, async (page) => {
    assert.equal((await pill(page, "Platform").textContent()).trim(), "Up");
    const model = pill(page, "N-ATLAS model");
    assert.equal((await model.textContent()).trim(), "Not reachable");
    assert.match((await model.getAttribute("class")) ?? "", /pill--warning/);
    assert.match(await page.locator(".status__note").innerText(), /provider_unavailable/);
  }));

await step("live status: a platform fault is reported as such", () =>
  visit({ ready: "down" }, async (page) => {
    const platform = pill(page, "Platform");
    assert.equal((await platform.textContent()).trim(), "Having problems");
    assert.match((await platform.getAttribute("class")) ?? "", /pill--danger/);
    assert.equal((await pill(page, "N-ATLAS model").textContent()).trim(), "Unknown");
  }));

await step("live status: when it cannot be loaded the page says so and stays usable", () =>
  visit({ ready: "dropped" }, async (page) => {
    assert.equal((await pill(page, "Platform").textContent()).trim(), "Unknown");
    assert.match(await page.locator(".status__note").innerText(), /could not be loaded/);
    await page.getByRole("link", { name: "Create an account", exact: true }).first().waitFor();
  }));

await step("signed-out visitors see sign-up, in the hero and again at the end", () =>
  visit({ signedIn: false }, async (page) => {
    const links = page.getByRole("link", { name: "Create an account", exact: true });
    assert.equal(await links.count(), 2);
    for (const href of await links.evaluateAll((els) => els.map((a) => a.getAttribute("href")))) assert.equal(href, "/register");
    assert.equal(await page.getByRole("link", { name: "Open your dashboard" }).count(), 0);
  }));

await step("signed-in visitors are offered the dashboard instead", () =>
  visit({ signedIn: true }, async (page) => {
    await page.getByRole("link", { name: "Open dashboard", exact: true }).waitFor();
    const links = page.getByRole("link", { name: "Open your dashboard", exact: true });
    assert.equal(await links.count(), 2);
    for (const href of await links.evaluateAll((els) => els.map((a) => a.getAttribute("href")))) assert.equal(href, "/app");
    assert.equal(await page.getByRole("link", { name: "Create an account", exact: true }).count(), 0);
  }));

await step("the capability tables list what is available and what is in development", () =>
  visit({}, async (page) => {
    const tables = page.locator("#platform .layers table");
    assert.equal(await tables.count(), 2);
    const names = (i) => tables.nth(i).locator("tbody th").allTextContents();
    assert.deepEqual(await names(0), ["API gateway", "Projects and API keys", "Playground", "Evaluation Lab", "Observability", "Documentation"]);
    assert.deepEqual(await names(1), ["Request logs and charts", "SDKs", "Voice", "MCP"]);
    const pills = (i) => tables.nth(i).locator("tbody .pill").allTextContents();
    assert.ok((await pills(0)).every((t) => t.trim() === "Available"));
    assert.ok((await pills(1)).every((t) => t.trim() === "In development"));
  }));

await step("every anchor the navigation uses exists, and the headings run in order", () =>
  visit({}, async (page) => {
    for (const id of ["platform", "start", "tools", "n-atlas", "languages", "voice", "mcp", "early-access", "security", "credits"]) {
      assert.equal(await page.locator(`[id="${id}"]`).count(), 1, `#${id}`);
    }
    const nav = await page.locator(".site-nav a").evaluateAll((els) => els.map((a) => a.getAttribute("href")));
    assert.deepEqual(nav, ["/#platform", "/#start", "/#languages", "/docs", "/security"]);
    const h2 = await page.locator("main h2").allTextContents();
    assert.deepEqual(h2.slice(0, 4), ["Request builder", "What you can use today", "Start in four steps", "Check quality before you build around it"]);
  }));

await step("early access states the real limits and the way to get help", () =>
  visit({}, async (page) => {
    const text = await page.locator("#early-access").innerText();
    for (const fact of ["provider_unavailable", "1,000 active end users", "60 requests per minute", "Email verification, password reset", "request ID"]) {
      assert.ok(text.includes(fact), `mentions ${fact}`);
    }
    const contact = page.locator("#early-access").getByRole("link", { name: "contact StackTechnology" });
    assert.match(await contact.getAttribute("href"), /^(mailto:|https:\/\/github\.com\/)/);
    assert.equal(await page.locator("#early-access").getByRole("link", { name: "privacy summary" }).getAttribute("href"), "/privacy");
  }));

await step("desktop layout: title and the lead sit side by side, status under the actions", () =>
  visit({}, async (page) => {
    const box = async (sel) => page.locator(sel).first().boundingBox();
    const [title, lead, actions, status] = await Promise.all([box(".hero__title"), box(".hero__lead"), box(".hero__actions"), box(".hero__status")]);
    assert.ok(title.x + title.width <= lead.x + 1, "title is left of the lead");
    assert.ok(actions.y > lead.y && status.y > actions.y, "lead, actions, status run top to bottom");
    assert.ok(Math.abs(status.x - lead.x) < 2, "status shares the lead column");
    for (const [name, sel] of [["platform", "#platform"], ["start", "#start"], ["tools", "#tools"], ["early-access", "#early-access"]]) {
      await page.locator(sel).screenshot({ path: `shots/landing-desktop-${name}.png` });
    }
    await page.locator("section", { has: page.locator("#start-building-title") }).screenshot({ path: "shots/landing-desktop-cta.png" });
  }));

await step("phone layout: no sideways scroll and every section stays inside the screen", () =>
  visit({}, async (page) => {
    const report = await overflowReport(page);
    assert.ok(report.scrollWidth <= report.innerWidth, `page is ${report.scrollWidth}px wide in ${report.innerWidth}px: ${report.wide.join("; ")}`);
    await page.screenshot({ path: "shots/landing-mobile-top.png" });
    await page.locator("#platform").screenshot({ path: "shots/landing-mobile-platform.png" });
    await page.locator("#early-access").screenshot({ path: "shots/landing-mobile-early-access.png" });
  }, { width: 390, height: 844 }));

await step("tablet layout: no sideways scroll and the capability tables keep a readable middle column", () =>
  visit({}, async (page) => {
    const report = await overflowReport(page);
    assert.ok(report.scrollWidth <= report.innerWidth, `page is ${report.scrollWidth}px wide in ${report.innerWidth}px: ${report.wide.join("; ")}`);
    const middle = await page.locator("#platform .layers table").first().locator("thead th").nth(1).boundingBox();
    assert.ok(middle.width >= 240, `"What it does" column is ${Math.round(middle.width)}px wide`);
    await page.locator("#platform").screenshot({ path: "shots/landing-tablet-platform.png" });
  }, { width: 820, height: 1000 }));

await step("the console stays free of errors and warnings", async () => {
  assert.deepEqual(consoleProblems, []);
});

await browser.close();
server.close();
const failed = results.filter(([r]) => r === "FAIL");
console.log(`\n${results.length - failed.length} of ${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
