# Integration contract with the core platform

The core platform (Agent 1: backend, database, authentication, projects, API keys, the N-ATLAS gateway, the dashboard) owns its own architecture.
This package does not redesign any of it. It consumes small interfaces (ports) and ships adapters for standalone use. This page is the list of what
the platform provides, what this package provides, and the proposed database tables and routes.

**Status: written without access to the platform's repository.** Everything below is a proposal to be matched to the platform's real conventions
(framework, router style, migration tool, naming). When the repository is available, inspect it first and adjust this page, never the other way round.

## Ports the platform implements (`src/forge_integrations/ports.py`)

| Port | Used by | Contract |
| --- | --- | --- |
| `ModelGateway.generate(GenerationRequest) -> GenerationResult` | MCP `generate`/`chat`/`translate`/`evaluate`, Evaluation Lab, voice replies | Calls N-ATLAS through the platform's gateway. Raises `ForgeError` on failure (use `provider_error`, `provider_timeout`, `provider_unavailable`). Must not log prompts. `GenerationRequest.source` is `"evaluation"`, `"mcp"`, `"voice"` or `"api"` so the gateway can tag its logs. |
| `AsrProvider.transcribe(...)` | MCP `transcribe`, voice pipeline | Provided here as `HttpAsrProvider` against the Forge ASR worker. The platform only supplies the worker's URL and token. |
| `RateLimiter.check(key, limit, window_seconds) -> RateDecision` | MCP | The platform's Redis (Memurai on Windows) limiter. `InMemoryRateLimiter` is provided for a single process. |
| `AuditSink.record(event)` | MCP | Writes audit events to the platform's audit log or table. `JsonlAuditSink` is provided. |
| `UsageReader.summary(project_id, days)` | MCP `get_usage` | Reads aggregates from the platform's own request logs. Returns only counts and aggregates. |
| `ProjectInfoProvider.get(project_id)` | MCP `get_project_info` | Optional. Return only fields safe to show to any holder of a token for the project. |
| `McpClientStore` | MCP | Persistence for MCP clients (table below). `SqliteMcpClientStore` and `InMemoryMcpClientStore` are provided. |
| `PolicyProvider.get(project_id) -> McpPolicy` | MCP | Per-project switch and scope narrowing. Wrap a database lookup in `CachedPolicyProvider` (modern clients trigger two lookups per call). |
| `RunStore` | Evaluation `RunManager` | `save`, `get(project_id, run_id)`, `list(project_id, limit)`. Every method is scoped to a project. |
| `Observer.emit(event)` | all | Receives structured, already redacted events. Must not block. `JsonLogObserver` is provided. |
| `ProjectAuthenticator.authenticate(...)` | platform-side routes for Evaluation and Voice | The platform's own project API key or session check. This package never authenticates platform users. |

Rules for every adapter: never log credentials, prompts, audio or transcripts; map failures to `ForgeError` with a stable code; keep every query scoped by `project_id`.

## What this package provides to the platform

- `forge_integrations.evaluation`: dataset validation, check catalogue and JSON Schema, runner, run manager, metrics, comparison, JSON and CSV export, starter datasets.
- `forge_integrations.voice`: audio validation, ASR client, pipeline. `forge_integrations.asr_service`: the worker.
- `forge_integrations.mcp`: the server (`create_mcp_app`), token service (`McpTokenService`), CLI, policy, audit.
- `forge_integrations.security`: redaction, token hashing, host and origin checks, SSRF-safe outbound, safe temp files.
- `forge_integrations.errors`: `ForgeError` (`code`, `message`, `http_status`, `retryable`, `details`; `to_payload(request_id)` gives `{"code","message","request_id"}`), `ValidationFailed` (a list of `ValidationIssue(path, message)`, answer it with 422), `ConfigError`.

Error responses on platform routes should be `{"error": {"code", "message", "request_id"}}` using `ForgeError.to_payload`, status from `ForgeError.http_status`.

## Identifiers

Platform identifiers are UUIDs. `McpTokenService` creates `client_id` as a UUID string. `RunManager` creates run ids with `uuid4().hex` by default; construct it with
`new_id=lambda: str(uuid.uuid4())` so run ids are UUID strings that fit a `uuid` column and the route parameters. Project ids are passed in from the platform's
authentication result and must match `[A-Za-z0-9_.:-]{1,64}` (a UUID does).

## Proposed PostgreSQL tables

Table names follow the master specification's list (`evaluations`, `evaluation_cases`, `evaluation_runs`, `voice_requests`, `mcp_clients`, `audit_logs`). Do not rename them.
Adjust the `projects` and `users` references and the migration tool to the platform's conventions. Plain SQL is given so it can be translated. Every table is scoped by project.

```sql
-- MCP clients: one row per issued token. The token itself is never stored, only its HMAC.
CREATE TABLE mcp_clients (
    client_id     uuid PRIMARY KEY,
    project_id    uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    name          text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    token_prefix  char(8) NOT NULL UNIQUE,
    token_hash    char(64) NOT NULL,
    scopes        text[] NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz,
    revoked_at    timestamptz,
    last_used_at  timestamptz,
    created_by    uuid REFERENCES users (id) ON DELETE SET NULL
);
CREATE INDEX idx_mcp_clients_project ON mcp_clients (project_id, created_at DESC);

-- Per-project MCP policy (optional; without a row the default is enabled with every scope the token holds).
CREATE TABLE mcp_project_policies (
    project_id      uuid PRIMARY KEY REFERENCES projects (id) ON DELETE CASCADE,
    enabled         boolean NOT NULL DEFAULT true,
    allowed_scopes  text[] NOT NULL DEFAULT ARRAY['models:read','project:read'],
    updated_at      timestamptz NOT NULL DEFAULT now(),
    updated_by      uuid REFERENCES users (id) ON DELETE SET NULL
);

-- An evaluation (the dataset): its identity. The cases are separate rows.
CREATE TABLE evaluations (
    id           uuid PRIMARY KEY,
    project_id   uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    name         text NOT NULL,
    description  text NOT NULL DEFAULT '',
    version      text NOT NULL DEFAULT '1',
    created_by   uuid REFERENCES users (id) ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, name, version)
);

CREATE TABLE evaluation_cases (
    id             uuid PRIMARY KEY,
    evaluation_id  uuid NOT NULL REFERENCES evaluations (id) ON DELETE CASCADE,
    position       integer NOT NULL,
    case_key       text NOT NULL,                 -- Case.id, unique inside the evaluation
    name           text NOT NULL,
    category       text NOT NULL DEFAULT 'general',
    language       char(2) CHECK (language IN ('en', 'ha', 'ig', 'yo')),
    prompt         text NOT NULL,
    system_prompt  text,
    repeat         smallint NOT NULL DEFAULT 1 CHECK (repeat BETWEEN 1 AND 5),
    max_tokens     integer,
    temperature    numeric(3, 2),
    tags           text[] NOT NULL DEFAULT '{}',
    checks         jsonb NOT NULL,                -- the list of {type, ...params}
    UNIQUE (evaluation_id, case_key),
    UNIQUE (evaluation_id, position)
);

-- Runs: summary columns for lists, the full run as JSON, and a snapshot of the cases that were run,
-- so editing an evaluation later cannot change what a stored run meant.
CREATE TABLE evaluation_runs (
    id                uuid PRIMARY KEY,
    project_id        uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    evaluation_id     uuid REFERENCES evaluations (id) ON DELETE SET NULL,
    dataset_name      text NOT NULL,
    dataset_version   text NOT NULL,
    dataset_snapshot  jsonb NOT NULL,             -- Dataset.to_dict()
    model             text,
    prompt_version    text,
    status            text NOT NULL CHECK (status IN ('completed', 'cancelled')),
    started_at        timestamptz NOT NULL,
    finished_at       timestamptz NOT NULL,
    summary           jsonb NOT NULL,
    run               jsonb NOT NULL,             -- Run.to_dict(): results, config, methodology
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_evaluation_runs_project ON evaluation_runs (project_id, started_at DESC);

-- Voice requests: facts about each request. No audio and no transcript unless the product explicitly decides to keep them.
CREATE TABLE voice_requests (
    id                uuid PRIMARY KEY,
    project_id        uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    request_id        text NOT NULL,
    language          char(2) NOT NULL CHECK (language IN ('en', 'ha', 'ig', 'yo')),
    audio_format      text,
    audio_size_bytes  integer,
    audio_sha256      char(64),
    audio_seconds     numeric(6, 3),
    asr_model         text,
    status            text NOT NULL CHECK (status IN ('ok', 'error')),
    error_code        text,
    steps             jsonb NOT NULL,             -- VoiceResult.steps: name, status, duration_ms, error_code
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_voice_requests_project ON voice_requests (project_id, created_at DESC);
```

`audit_logs` is the platform's own table. The MCP `AuditSink` adapter writes the `mcp.*` events into it (event name, project, actor or client, request id, a small JSON of names, sizes, codes and timings, timestamp), and nothing that could hold content.

Adapters in short:

- `RunStore.save(run)` inserts into `evaluation_runs` using `run_summary(run)` for the columns, `run.to_dict()` for `run`, and the dataset that was run for `dataset_snapshot`. `get` selects by `(project_id, id)`. `list` returns the summary columns ordered by `started_at DESC`.
- Loading an evaluation to run: select the `evaluations` row and its `evaluation_cases` ordered by `position`, build the dataset document, and pass it through `validate_dataset` again. Never trust stored rows to be valid.
- `McpClientStore`: one method, one statement. `revoke` is `UPDATE ... SET revoked_at = $1 WHERE project_id = $2 AND client_id = $3 AND revoked_at IS NULL` and returns whether a row changed.

## Proposed routes (to match the platform's router)

All under `/api/v1`, all authenticated by the platform, all scoped to the caller's project, all answering with the error shape above. Names follow the specification's navigation (Evaluations, Voice, MCP).

| Route | Purpose | Uses |
| --- | --- | --- |
| `GET /evaluation/checks` | Check catalogue for form builders | `evaluation.catalogue.check_catalogue()` |
| `GET /evaluation/schema` | JSON Schema of a dataset | `dataset_json_schema()` |
| `GET /evaluation/starter-datasets`, `GET /evaluation/starter-datasets/{id}` | Examples to copy | `describe_starter_datasets()`, `load_starter_document(id)` |
| `POST /projects/{project_id}/evaluations` | Create an evaluation with its cases (validate first, 422 with issues) | `validate_dataset` |
| `GET /projects/{project_id}/evaluations`, `GET`/`PUT`/`DELETE .../{evaluation_id}` | Manage evaluations | |
| `POST /projects/{project_id}/evaluations/{evaluation_id}/runs` | Start a run, answer `202` with the handle | `RunManager.start` |
| `GET /projects/{project_id}/evaluations/{evaluation_id}/runs` | List run summaries | `RunManager.list_runs` |
| `GET /projects/{project_id}/evaluation-runs/{run_id}` | Progress while running, the run when finished | `status`, `get_run` |
| `POST /projects/{project_id}/evaluation-runs/{run_id}/cancel` | Cancel | `cancel` |
| `GET /projects/{project_id}/evaluation-runs/{a}/compare/{b}` | Compare two runs | `compare_runs` |
| `GET /projects/{project_id}/evaluation-runs/{run_id}/export?format=csv\|json` | Download | `export_csv`, `export_json` (send with a safe file name: `safe_download_filename`) |
| `POST /projects/{project_id}/voice/transcriptions` | `multipart/form-data`: `file`, `language` | `VoicePipeline.transcribe` |
| `POST /projects/{project_id}/voice/responses` | Transcribe, then reply with the language model | `VoicePipeline.transcribe_and_respond` |
| `GET`/`POST /projects/{project_id}/mcp/clients`, `DELETE .../{client_id}` | List (never secrets), create (token shown once), revoke | `McpTokenService.list_clients/issue/revoke` |
| `GET`/`PUT /projects/{project_id}/mcp/policy` | Read or change the project's MCP policy | `McpPolicy` |

Uploads on the voice routes: read the body with a hard size limit before parsing (see `web/limits.py`), take one file and a few small fields, hand the bytes to the pipeline,
never write them under the client's name.

## Events to forward to the platform's logs

Observer events (names only; payloads carry ids, sizes, codes and timings, never content): `mcp.tool_call`, `mcp.policy_denied`, `mcp.auth_failure`, `mcp.auth_throttled`,
`mcp.request_limited`, `mcp.token_issued`, `mcp.token_revoked`, `mcp.audit_failure`, `mcp.tool_exception`, `mcp.server_starting`, `evaluation.run.started`, `evaluation.attempt`,
`evaluation.run.finished`, `evaluation.run.ended`, `evaluation.run.failed`, `evaluation.run.cancel_requested`, `evaluation.run.persist_failed`, `voice.step`, `asr.request`.

## Product copy rules for screens built on this package

- Show the model attribution strings from `natlas_facts.py` (`LLM_REQUIRED_ATTRIBUTION`, `ASR_MODELS[...].required_attribution`) where the model's output appears.
- State the 1,000 active end-user limit (rolling 30 days) wherever deployment or production use is discussed, and do not describe the platform as an enterprise or commercial system.
- Label evaluation results as "checks passed", never "accuracy". Show the methodology notes that come with every run.
- Label translations as machine output with unmeasured quality (the MCP tool already does).
- Do not expand the N-ATLAS acronym, do not claim streaming, additional languages or certifications.
- No em dashes in visible product copy.

## Environment variables to add to the platform's configuration

See `.env.integrations.example` in the package root. Merge the names into the platform's own `.env.example` by hand. This package never edits it.
