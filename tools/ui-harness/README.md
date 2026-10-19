# UI harness

Real-browser checks for the web app in a sandbox that has no Vite and no network. It bundles the **real** pages and the **real** app shell with esbuild, replaces only the router and the extension registry with small stand-ins, serves the real stylesheets, answers `/api/v1/*` from a mock server, and drives Chromium with Playwright.

It needs two global tools: `tsx` (which carries esbuild) and `playwright` with a Chromium. The folder defaults to `/home/claude/.npm-global/lib/node_modules`; set `GLOBAL_NODE_MODULES` to change it. Nothing here is part of the production image.

```bash
python3 tools/ui-harness/make_fixtures.py   # real engine output used as test data (written to dist/)
node tools/ui-harness/build.mjs             # bundle the pages
node tools/ui-harness/pg.test.mjs           # Playground: 18 checks
node tools/ui-harness/ev.test.mjs           # Evaluation Lab: 25 checks
node tools/ui-harness/landing.test.mjs      # Landing page: 15 checks
node tools/ui-harness/dash.test.mjs         # Dashboard: 12 checks
node tools/ui-harness/docs.test.mjs         # Documentation page: 4 checks
```

Screenshots land in `tools/ui-harness/shots/`. Look at them: assertions do not prove a layout looks right.

## Adding a page

1. Import the page in `entry.tsx` and add it to `pages`. The query string picks it, and every other query parameter becomes a route parameter (`/?page=run&projectId=...&runId=...`). Public pages render in the public shell: add `&layout=public` (`/?page=landing&layout=public`).
2. If the page uses a router hook the stand-in lacks, add it to `router-stub.tsx`.
3. Write a test file like `ev.test.mjs`: a mock API that answers only what the page calls, `step(...)` blocks, and a final check that the console is clean (allow only the 4xx and 5xx loads you provoke on purpose).

## Lessons that cost time

- Locators: `getByText` is a substring match and throws when it finds several elements. Scope with `locator(".class", { hasText })` when the same words appear twice (a stat label and a table header, a message and a raw JSON panel).
- Measure layout, do not guess. At 390 px compare `document.documentElement.scrollWidth` with `window.innerWidth`, and when they differ list every element whose right edge is past the viewport and walk up its ancestors printing `display`, `grid-template-columns` and `min-width`. Three real bugs came from this: a grid track that followed the widest table, a nav bar in a `1fr` track, and absolutely positioned visually hidden labels escaping a scrolling wrapper.
- The browser logs a failed network load as a console error. Filter the statuses you provoke on purpose, and fail on everything else.
- Use `useAsyncData` carefully: `reload` is a new function on every render and a reload sets `loading` while keeping `data`. Drive polling with a tick in the dependency list, not with `reload` in an effect.
- A grid that holds tables needs `grid-template-columns: minmax(0, 1fr)`, or its track follows the widest table. Test both sides of every breakpoint (390, 820 and 1280 px), not only the phone: a fix can be hidden by a second rule that also happens to prevent the overflow.
- An element screenshot of a page with a sticky header shows the header over the element. Look past it.
- `innerText` puts a line break after a visually hidden span, because absolute positioning makes it block-level. Match with `\s*`, not a space.

## Backend logic without a web framework

`tools/checks/sandbox_run.py` runs test modules that need no database in a sandbox without FastAPI, SQLAlchemy or Starlette. It stubs **import names only**, so the code under test (real `AppError`, real gateway contracts, real evaluation library, real module files) is the real code:

```bash
python3 tools/checks/sandbox_run.py tests.test_evaluation_mapping tests.test_evaluation_adapter
```

Write such tests as plain functions that use `asyncio.run`, with no fixtures and no `pytest` import. Check that a new test has teeth by breaking the code on purpose in a copy of the tree (set `FORGE_ROOT` to the copy) and confirming that the right tests fail.
