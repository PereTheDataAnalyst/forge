# Evaluation Lab engine

The engine runs a developer-written dataset against a model and reports which of the developer's own checks held.
That is all it claims. There is **no accuracy score and no model-based judge.**

- `pass_rate` is the share of completed cases whose checks all held. It is not model accuracy.
- A `fail` means the output matched none of the forms the dataset listed. It never means "the model is wrong".
- Heuristic checks (`language_match`) report `review` when they cannot decide, so a human looks.
- A model call that fails gives `error`, reported separately from `fail`, so provider trouble is not mistaken for a quality result.

The methodology text is part of every run (`run.methodology`) so an exported report carries its own limits.

## Dataset format

```json
{
  "name": "Greetings",
  "description": "Short greetings in four languages.",
  "version": "1",
  "cases": [
    {
      "id": "yo-good-morning",
      "name": "Good morning in Yoruba",
      "category": "language",
      "language": "yo",
      "prompt": "Translate to Yoruba: Good morning",
      "system": "Answer with the translation only.",
      "repeat": 1,
      "max_tokens": 64,
      "temperature": 0.1,
      "tags": ["greeting"],
      "checks": [
        { "type": "contains_any", "values": ["E kaaro", "Ẹ kú àárọ̀"], "ignore_diacritics": true },
        { "type": "not_empty" }
      ]
    }
  ]
}
```

Required: dataset `name` and `cases`; per case `name`, `prompt`, `checks`. Unknown fields are rejected, and every problem is
reported at once with a path such as `cases[2].checks[0].values`. Categories: `factual`, `language`, `consistency`, `safety`,
`format`, `general`. Languages: `en`, `ha`, `ig`, `yo`.

Limits (`EvaluationLimits`, tightened by the MCP tool): up to 200 cases, prompt 8,000 characters, system 4,000, 12 checks per case,
`repeat` up to 5, 10 tags, `max_tokens` up to 1,024, 600 model calls in total (the sum of `repeat`).

## Checks

Text checks accept `ignore_case` (default true), `ignore_diacritics` (default false), `normalize_whitespace` (default true) and
`strip_punctuation` (default false). `ignore_diacritics` folds tone marks, under-dots and the Hausa hooked letters, and
apostrophe variants, so a check compares meaning instead of keyboard habits.

| Check | Holds when |
| --- | --- |
| `exact_match` (`expected`) | The output equals `expected` after normalisation. |
| `contains_all` (`values`) | Every string appears. |
| `contains_any` (`values`) | At least one string appears. |
| `not_contains` (`values`) | None appears. |
| `starts_with` / `ends_with` (`value`) | The output starts or ends with it. |
| `length_between` (`min_chars`, `max_chars`) | Trimmed length is in range (give at least one bound). |
| `word_count_between` (`min_words`, `max_words`) | Word count is in range. |
| `json_valid` (`required_keys`, `allow_code_fence`) | The output parses as JSON and has the keys. A `json` code fence is accepted by default. |
| `numeric_within` (`expected`, `tolerance`, `which`) | The first (or last) number in the output is within the tolerance. |
| `language_match` (`language`) | A heuristic agrees. See the caveat below. |
| `no_secrets` | No bearer token, API key or similar secret-like string appears. |
| `no_contact_details` | No email address or phone number appears. |
| `consistency` (`min_similarity`, default 0.8) | Repeated attempts of one case give similar outputs. The case needs `repeat` of 2 or more. |
| `max_latency_ms` (`limit_ms`) | The slowest successful attempt finished within the limit. |
| `not_empty` (`min_chars`) | The output has at least that many characters. |

Caveats that matter:

- `language_match` is **not** a language-identification model and has not been validated against a labelled corpus. It looks at function words and
  orthographic markers (Yoruba ẹ ṣ, Igbo ị ụ ṅ, Hausa ɓ ɗ ƙ ƴ, the under-dotted ọ shared by Yoruba and Igbo). It returns `review` when the evidence is thin,
  and needs at least 4 words. The word lists are small and should be reviewed by native speakers before anyone relies on them.
- `consistency` is mean pairwise token similarity (`difflib`), a measure of repetition, not of correctness.
- `no_secrets` and `no_contact_details` are pattern checks. They catch common shapes, not every possible leak.

For form builders: `evaluation.catalogue.check_catalogue()` lists every check with its parameters, and
`dataset_json_schema()` returns a JSON Schema generated from the validator's own specifications, so the two cannot drift. The schema is never
stricter than the validator.

## Statuses and metrics

Per case: `pass`, `fail`, `review`, `error`, `skipped` (a cancelled run). Run summary: `total`, `counts`, `completed`, `pass_rate`,
`check_failure_rate`, `review_rate`, `error_rate`, groupings `by_category` and `by_language`, `attempts` (total, failed, failure rate, errors by code),
`latency_ms` (min, mean, p50, p95, max, nearest-rank, measured by the runner around the gateway call, successful attempts only), per-check counts
and the mean consistency. `compare_runs(a, b)` compares two runs case by case (`improvement`, `regression`, `unchanged`, `not_comparable`,
`missing_in_a`, `missing_in_b`) with deltas for pass rate, error rate and latency percentiles.

Stored outputs are cut at 50,000 characters and flagged `output_truncated`.

## Running

The runner needs only a `ModelGateway` (see `ports.py`):

```python
from forge_integrations.evaluation import EvaluationRunner, RunConfig, load_starter_dataset

runner = EvaluationRunner(gateway)
run = await runner.run(load_starter_dataset("starter-general-checks"), RunConfig(max_concurrency=2, prompt_version="v1"))
print(run.summary["pass_rate"])
```

For a web backend use `RunManager`: `start(project_id, dataset, config)` returns a handle at once and runs in the background, `status` reports
progress, `get_run` returns the finished run, `list_runs`, `cancel`, `wait`, `shutdown`. Rules it enforces: every lookup takes the project id and a run
of another project looks exactly like a run that does not exist; runs in progress are capped per project (`EVAL_MAX_CONCURRENT_RUNS_PER_PROJECT`, default 2)
and in total; each run has a hard deadline; failures become a handle in state `failed` with a generic message, never a traceback. Persistence is the
`RunStore` port, so the platform supplies one backed by its database. `InMemoryRunStore` is for tests and single-process use.

Settings (`load_evaluation_settings`): `EVAL_MAX_CASES` (200), `EVAL_MAX_CONCURRENCY` (4), `EVAL_CALL_TIMEOUT_SECONDS` (90), `EVAL_MAX_CONCURRENT_RUNS_PER_PROJECT` (2).

From a terminal: `examples/evaluation/run_dataset.py` (validate, plan with `--dry-run`, run against a gateway factory, write JSON and CSV).

## Exports

`export_json(run)` is the full run. `export_csv(run)` has one row per case and neutralises spreadsheet formulas: a cell that starts with `=`, `+`, `-`, `@`, tab or
carriage return gets a leading apostrophe, so opening an exported file in a spreadsheet cannot run a formula that came from model output.

## Starter datasets

Two datasets ship with the package (`starter_dataset_ids()`, `load_starter_dataset(id)`). They are examples to copy and edit, **not benchmarks**.

- `starter-general-checks`: 8 cases covering factual answers, simple arithmetic, JSON output, consistency, no contact details, a canary that checks a system prompt is not leaked, and a latency budget.
- `starter-nigerian-languages`: 15 cases. Five translate into English, six translate from English into Hausa, Igbo or Yoruba with several accepted forms and `ignore_diacritics`, and four `language_match` cases.

The expected answers were written for these datasets and have not been reviewed by native speakers. Native-speaker review is needed before
anyone uses the language cases to judge a model, and the accepted-answer lists are short on purpose: a correct answer in an unlisted form will `fail`.
The language model card reports a human-evaluation average of 2.69 out of 5 for Yoruba, so some Yoruba failures are expected.

## What is not here

- No accuracy, BLEU, WER or chrF figure, and no model-as-judge. If a team adds one later it must be labelled as what it is and validated first.
- No persistence beyond the ports. Tables, routes and screens belong to the platform (see `integration-contract.md` for the proposed schema and routes).
- Tested against a scripted gateway only. No real N-ATLAS output was evaluated, because that needs the platform's gateway or N-ATLAS API credentials.
