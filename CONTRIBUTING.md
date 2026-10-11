# Contributing

1. Read [docs/architecture.md](docs/architecture.md) and [docs/branching-and-handoff.md](docs/branching-and-handoff.md).
2. Branch from `develop` as `feature/<area>-<summary>`. Never push to `main`.
3. Keep changes small and commit with `type(scope): summary`.
4. Run `.\scripts\check.ps1` before opening a pull request. It runs ruff, `alembic check`, pytest, the TypeScript type check, ESLint and the production build.
5. A database change needs a new Alembic revision that matches the models. Never edit an applied revision.
6. Do not rename tables, routes, settings or components without a documented reason.
7. Public copy follows the rules in [docs/status.md](docs/status.md): no invented metrics, no em dashes, honest status labels.
8. Never commit `.env`, tokens, keys, model weights or logs.

Extension modules (evaluation, voice, MCP) follow [docs/extension-modules.md](docs/extension-modules.md).
