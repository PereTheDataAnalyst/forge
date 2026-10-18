# Extension modules contract

**Status: the Evaluation module is wired (`backend/app/modules/evaluation/`, `frontend/src/extensions/evaluation/`). Voice and MCP are not.** The Evaluation, Voice and MCP modules plug into the core platform without editing core files. This page is the contract between the core platform build (Agent 1) and the advanced integrations build (Agent 2). If something is missing here, ask for it as a change to the core instead of working around it.

## Ownership

| Owner | Paths |
| --- | --- |
| Core platform (Agent 1) | Everything not listed below |
| Advanced integrations (Agent 2) | `backend/app/modules/<name>/`, `backend/tests/modules/<name>/`, `frontend/src/extensions/<name>/`, new Alembic revisions after `0002`, `docs/modules/` |

Do not rename tables, routes, settings or components. Do not add a second web framework, ORM or state library.

## Backend module

Create `backend/app/modules/<name>/__init__.py` with `name` one of `evaluation`, `voice` or `mcp`. The loader (`app/integrations/loader.py`) imports it at startup. A missing module is skipped and a module that fails to import raises.

```python
from fastapi import APIRouter

router = APIRouter(prefix="/evaluations", tags=["evaluations"])   # mounted under /api/v1

def register(app):          # optional
    ...
```

Inside `register(app)` a module may:

| Need | How |
| --- | --- |
| Call N-ATLAS | `request.app.state.gateway.chat(principal, GatewayChatInput(...), request_id=..., endpoint=...)`. Never call the provider directly, so scope checks, limits, request logs and usage stay uniform. |
| Identify the caller | `PrincipalDep` (API key) or `AuthDep` (browser session) from `app.api.deps`. Build a `GatewayPrincipal` with `source` set to `evaluation`, `voice`, `mcp` or `playground`. |
| Add credential scopes | `app.core.scopes.register_scope("asr.transcribe")` at import time. Planned names: `asr.transcribe`, `evaluations.run`, `mcp.invoke`. |
| Larger request bodies | `app.state.body_limit_overrides["/api/v1/audio"] = 10 * 1024 * 1024` |
| Startup and shutdown work | Append coroutine functions to `app.state.startup_hooks` and `app.state.shutdown_hooks`. Each receives the app. |
| Register a speech provider | `app.state.integrations.asr_provider = MyAsrProvider(...)` implementing `AsrProvider` from `app.providers.base`. |
| Feed the dashboard | `app.state.integrations.recent_evaluation_runs = fn` with signature `(session, user_id, limit) -> list[dict]` returning keys `id`, `evaluation_name`, `status`, `created_at` (ISO 8601) and `model`. |
| Database access | `DbDep` for request-scoped sessions, or `app.state.session_factory` for background work. |

Rules that apply to every module:

- Authenticate and authorise every route. Validate every input with Pydantic and `extra="forbid"`.
- Return the standard error shape by raising errors from `app.core.errors`.
- Never log secrets, prompts, generated text, transcripts or audio. Use `app.core.logging.get_logger`.
- Store audio only in a temporary directory outside the repository and delete it after processing. Validate size, duration, MIME type and file signature.
- Do not claim a metric or score that is not produced by an implemented method. Evaluation reports measured values only.
- MCP tools must be read-only or generation-only. Never expose project deletion, key rotation, shell access or infrastructure changes.

## Database

The reserved tables `evaluations`, `evaluation_cases`, `evaluation_runs`, `evaluation_results`, `voice_requests`, `mcp_clients` and `system_settings` already exist from migration `0002` with models in `backend/app/db/models/integration.py`. Agent 2 may amend `0002` and those models until they are merged to `develop`. After that, add revisions with `down_revision = "0002"` (or the latest) and run `alembic check`.

## Frontend module

Create `frontend/src/extensions/<name>/module.tsx` with a default export:

```tsx
import type { FeatureModule } from "../registry";

const module: FeatureModule = {
  navItems: [{ to: "/app/evaluations", label: "Evaluations" }],
  routes: [{ path: "evaluations", element: <EvaluationsPage /> }],
};
export default module;
```

Routes are mounted under `/app` behind authentication. Reuse `components/`, `lib/api.ts` patterns and the design tokens in `styles/tokens.css`. Do not add colours that are not derived from the logo. When a capability ships, change its entry in `frontend/src/config.ts` (`layers`) from `in_development` to `available`.

## Verification before handing over

Run `.\scripts\check.ps1` and the dependency-free checks listed in `tools/checks/README.md`. Report what was executed and what was not.
