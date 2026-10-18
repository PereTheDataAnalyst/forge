import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { FRONTEND, HERE } from "./paths.mjs";

const TYPES = { ".js": "text/javascript", ".css": "text/css", ".png": "image/png", ".html": "text/html" };
const STYLES = ["tokens", "base", "components", "layout", "app"];

export function startServer(handleApi) {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://harness.local");
    if (url.pathname.startsWith("/api/")) return handleApi(req, res, url);
    if (url.pathname === "/" || url.pathname === "/app") {
      const links = STYLES.map((n) => `<link rel="stylesheet" href="/styles/${n}.css">`).join("");
      res.writeHead(200, { "content-type": "text/html" });
      return res.end(`<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>harness</title>${links}
<link rel="stylesheet" href="/dist/entry.css"></head><body><div id="root"></div>
<script>document.cookie = "forge_csrf=harness-csrf; path=/";</script>
<script src="/dist/entry.js"></script></body></html>`);
    }
    let file = null;
    if (url.pathname.startsWith("/dist/")) file = path.join(HERE, url.pathname);
    else if (url.pathname.startsWith("/styles/")) file = path.join(FRONTEND, "src", url.pathname);
    else if (url.pathname.startsWith("/brand/")) file = path.join(FRONTEND, "public", url.pathname);
    if (file && fs.existsSync(file) && fs.statSync(file).isFile()) {
      res.writeHead(200, { "content-type": TYPES[path.extname(file)] ?? "application/octet-stream" });
      return fs.createReadStream(file).pipe(res);
    }
    res.writeHead(404).end("not found");
  });
  return new Promise((resolve) =>
    server.listen(0, "127.0.0.1", () => resolve({ server, port: server.address().port })),
  );
}

export function json(res, status, body, headers = {}) {
  res.writeHead(status, { "content-type": "application/json", ...headers });
  res.end(JSON.stringify(body));
}

export function readBody(req) {
  return new Promise((resolve) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const text = Buffer.concat(chunks).toString("utf8");
      try { resolve(text ? JSON.parse(text) : null); } catch { resolve(null); }
    });
  });
}
