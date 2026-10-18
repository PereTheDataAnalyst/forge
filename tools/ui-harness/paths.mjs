// Paths shared by the harness scripts. Everything is relative to the repository, except the
// globally installed tools, whose folder can be overridden with GLOBAL_NODE_MODULES.
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

export const HERE = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(HERE, "..", "..");
export const FRONTEND = path.join(ROOT, "frontend");
export const GLOBAL_MODULES =
  process.env.GLOBAL_NODE_MODULES ?? "/home/claude/.npm-global/lib/node_modules";

const globalRequire = createRequire(path.join(GLOBAL_MODULES, "_"));
// esbuild ships inside the globally installed `tsx`; Playwright is installed globally.
export const esbuild = () => globalRequire(path.join(GLOBAL_MODULES, "tsx", "node_modules", "esbuild"));
export const playwright = () => globalRequire(path.join(GLOBAL_MODULES, "playwright"));
