// Bundles the real pages and the real app shell for a browser. Only the router and the
// extension registry (which needs Vite's import.meta.glob) are replaced by small stand-ins.
import fs from "node:fs";
import path from "node:path";
import { HERE, esbuild } from "./paths.mjs";

const registryStub = {
  name: "registry-stub",
  setup(build) {
    build.onResolve({ filter: /extensions\/registry$/ }, () => ({
      path: path.join(HERE, "registry-stub.ts"),
    }));
  },
};

fs.mkdirSync(path.join(HERE, "dist"), { recursive: true });
await esbuild().build({
  entryPoints: [path.join(HERE, "entry.tsx")],
  bundle: true,
  format: "iife",
  platform: "browser",
  target: "es2022",
  jsx: "automatic",
  outdir: path.join(HERE, "dist"),
  nodePaths: [process.env.GLOBAL_NODE_MODULES ?? "/home/claude/.npm-global/lib/node_modules"],
  alias: { "react-router-dom": path.join(HERE, "router-stub.tsx") },
  plugins: [registryStub],
  define: {
    "import.meta.env.VITE_CONTACT_EMAIL": "undefined",
    "process.env.NODE_ENV": '"development"',
  },
  logLevel: "warning",
});
console.log("bundle built");
