# Evaluation Lab

The Evaluation Lab (`/app/evaluations`) tests N-ATLAS against checks you write. A dataset is a list of prompts, and each prompt carries checks that the answer must meet. A run sends every case through the gateway to the real model and reports which checks held.

## What it measures, and what it does not

- Every check is a deterministic rule: the reply contains these words, is valid JSON, stays under a length, is in the right language by a heuristic, contains no secrets, answers within a time budget, and so on. The list comes from the engine and is shown in the web app under "Checks you can use".
- The **pass rate** is the share of completed cases whose checks all held. It is not model accuracy, and no other model judges the answers.
- A case ends as `pass`, `fail` (a check did not hold), `review` (a check could not decide, read the output yourself), `error` (the model call failed, so the case was not evaluated) or `skipped` (the run was cancelled before it started).
- Latency is measured around the gateway call. Failure rates are counted from real attempts. Nothing is estimated and nothing is invented. If the model host is away, the run is refused with a clear message, and a case whose call fails is recorded with the error code.
- The methodology text travels with every result and is shown under "How these numbers are made".

## Using it

1. Open **Evaluations** in the left bar and choose a project.
2. **Add dataset**: pick a starter, or paste JSON (the "Insert an example" button fills a valid one). The server validates it and names every problem with a field and a message.
3. Optionally set a **prompt version** label such as `v2` to tell runs apart. Letters, digits and `. _ : / @ + # -` only.
4. Press **Run**. The run page shows progress, and you can cancel: cases already running finish, and the rest are skipped.
5. Read the summary, the breakdown by language and category, and each case with the model's reply and the explanation of every check. **Needs attention** shows only failed, undecided and errored cases.
6. **Download CSV** or **Download JSON**. The CSV starts with a byte-order mark so Excel reads Hausa, Igbo and Yoruba text correctly.

## Datasets

A dataset is a JSON object with a `name`, an optional `description` and a list of `cases`. Each case has an `id`, a `category`, a `prompt` and a list of `checks`, and may have a display `name`, a `system` instruction, a `language` (`en`, `ha`, `ig` or `yo`), `repeat`, `max_tokens`, `temperature` and `tags`. The full rules are in [../integrations/docs/evaluation.md](../integrations/docs/evaluation.md) and in the engine's JSON schema.

Limits (all adjustable, see below): up to 100 cases per dataset, 50 active datasets per project, a name of 120 characters and a description of 500 (the column sizes of the `evaluations` table). Datasets are immutable: to change one, add a new one and archive the old one. The dataset's own version label is kept but not shown.

Two starters ship with Forge: general checks (8 cases) and English, Hausa, Igbo and Yoruba phrases (15 cases). They are examples, not benchmarks.

## Runs

- A run starts in the background and the request returns at once (202). The page polls every 1.5 seconds.
- One run per project at a time. A user can start 20 runs per hour. Each run makes at most two model calls at once, because the model host handles one generation at a time.
- Every attempt is a normal gateway request: scope check, rate limit, request log and usage metering are the same as for any caller. The request log records `source` `evaluation` and the project. It is not tied to a person or an API key, so the gateway rate limit is counted per project. A throttled call waits and tries again (up to three times, at most 15 seconds each) before the case is recorded as `rate_limited`.
- A run is refused up front when the chosen model is not available, so it never produces a page of errors. A run that takes longer than the allowed time is stopped and keeps the results it has.
- **Saving.** The engine marks a run completed just before it writes it to the database. `handle.persisted` is `null` while it is being saved, `true` once it is stored and `false` if the write failed. A run that could not be saved stays readable and exportable until the service restarts, and the page says so.
- **Restarts.** A run in progress when the service restarts (including a deploy) is lost: start it again. Finished runs are in the database and survive restarts.
- The runs list can miss a run for a moment between "completed" and "saved". The page looks again shortly after a run ends.

## What is stored

Request logs and the dashboard never contain prompts or responses. The Evaluation Lab is the one exception, by design, so you can inspect failed cases:

| Table | What it holds |
| --- | --- |
| `evaluations`, `evaluation_cases` | The datasets you add: name, description, and for each case the prompt, category, language, system instruction, and in `expected` (JSON) the display name, key, checks, repeat count, limits, tags and dataset version |
| `evaluation_runs` | One row per finished run: model, status, timestamps, and in `summary` (JSON) the dataset name and version, prompt version, configuration, metrics and methodology |
| `evaluation_results` | One row per case: status, the first successful reply in `output`, the slowest good latency, the error code, and in `checks` (JSON) the whole case result: every attempt, every check result and the error message |

No migration was needed: the tables came with migration 0002. Archiving a dataset hides it, and its runs stay. **Deleting runs from the web app is not available yet**, and the privacy page says so. `request_log_id` and `triggered_by` are not filled in.

Before storage, NUL characters (which PostgreSQL cannot hold) are removed from model output, and non-finite numbers become null.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `EVAL_MAX_CASES` | `100` | Most cases in one dataset |
| `EVAL_MAX_CONCURRENCY` | `2` | Model calls one run makes at once |
| `EVAL_CALL_TIMEOUT_SECONDS` | `90.0` | Time allowed for one model call |
| `EVAL_MAX_CONCURRENT_RUNS_PER_PROJECT` | `1` | Runs one project may have going |
| `EVAL_RUNS_PER_HOUR` | `20` | Runs one user may start per hour |
| `EVAL_MAX_RUN_SECONDS` | `900` | A longer run is stopped and keeps its results |
| `EVAL_MAX_EVALUATIONS_PER_PROJECT` | `50` | Active datasets per project |

## Endpoints

All of them use the browser session (cookie plus CSRF) and a project you own. They are listed in [api-reference.md](api-reference.md). Errors use the standard shape. Useful codes: `invalid_dataset` (422, with a field and a message for each problem), `evaluation_not_found`, `evaluation_limit_reached`, `provider_unavailable`, `model_not_found`, `rate_limited` (the project already has a run), `evaluation_rate_limited`, `evaluation_run_not_found`, `evaluation_run_not_running`, `evaluation_run_not_finished`.

## How it is built

The engine is the standalone library `forge_integrations.evaluation` in `integrations/` (no third-party dependencies). `backend/app/modules/evaluation/` adapts it to the platform:

- `mapping.py`: pure conversions between the engine's objects and the database rows, with no framework imports.
- `gateway_adapter.py`: lets the engine call `app.state.gateway`, looked up on every call.
- `store.py`: the engine's run store on PostgreSQL, always scoped to a project.
- `service.py`, `routes.py`, `schemas.py`, `errors.py`: dataset storage and the HTTP layer.
- `runtime.py`: the run manager, its startup and shutdown hooks, and the dashboard's recent-runs hook.

The run manager lives in one process, so the container keeps one worker (see [deployment.md](deployment.md)). The image installs the library before the backend (see the `Dockerfile`). Frontend: `frontend/src/extensions/evaluation/`.

## Tests

- `backend/tests/test_evaluation_mapping.py` and `test_evaluation_adapter.py` need no database. They run the real engine on its real starter datasets and push every run through the same conversions the store uses. `python tools/checks/sandbox_run.py tests.test_evaluation_mapping tests.test_evaluation_adapter` runs them without the web framework.
- `backend/tests/test_evaluation.py` needs PostgreSQL and Redis. It runs the real routes, session authentication, CSRF, ownership, the engine, the gateway, request logging, the database store and the dashboard, with the model replaced by a scripted provider that exists only in `backend/tests/provider_double.py`.
- `tools/ui-harness/ev.test.mjs` drives the real pages in Chromium against a mock API built from run data that the real engine produced. See [../tools/ui-harness/README.md](../tools/ui-harness/README.md).
- `scripts\smoke-day1.ps1` has an Evaluation Lab step (6c). With the model connected it adds a starter, runs it against the real model, waits for the saved result and exports it.

## Known limits

Datasets cannot be edited. Runs cannot be deleted, compared or scheduled from the web app. A run in progress is lost on restart. `request_log_id` and `triggered_by` are empty. A reply is stored only for the first successful attempt in `output`, although every attempt is in `checks`. There is no model-based judge, by choice.
