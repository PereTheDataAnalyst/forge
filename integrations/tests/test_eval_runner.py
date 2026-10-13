import asyncio
import csv
import io
import json
import unittest

from forge_integrations.errors import ErrorCode, ForgeError
from forge_integrations.evaluation import EvaluationRunner, RunConfig, compare_runs, validate_dataset
from forge_integrations.evaluation.export import csv_safe, export_csv, export_json
from forge_integrations.evaluation.metrics import latency_stats, percentile
from forge_integrations.ports import GenerationResult, Usage
from forge_integrations.security.tokens import generate_token


class FakeGateway:
    """Test double. Maps the user prompt to a response, an exception, or a callable."""

    def __init__(self, responses, delay=0.0):
        self.responses = responses
        self.delay = delay
        self.requests = []
        self.active = 0
        self.max_active = 0

    async def generate(self, request):
        self.requests.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            prompt = request.messages[-1].content
            value = self.responses.get(prompt, "default")
            if callable(value):
                value = value(request)
            if isinstance(value, BaseException):
                raise value
            return GenerationResult(text=value, model="test-double", usage=Usage(1, 2, 3), latency_ms=5.0, finish_reason="stop")
        finally:
            self.active -= 1


class Observer:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(dict(event))


def dataset():
    return validate_dataset(
        {
            "name": "smoke",
            "version": "v1",
            "cases": [
                {"id": "fact", "name": "capital", "category": "factual", "prompt": "capital?", "language": "en",
                 "checks": [{"type": "contains_any", "values": ["Abuja"]}, {"type": "max_latency_ms", "limit_ms": 60000}]},
                {"id": "wrong", "name": "wrong answer", "category": "factual", "prompt": "largest?", "checks": [{"type": "contains_any", "values": ["Lagos"]}]},
                {"id": "lang", "name": "hausa", "category": "language", "prompt": "hausa?", "language": "ha",
                 "checks": [{"type": "language_match", "language": "ha"}]},
                {"id": "safe", "name": "no secrets", "category": "safety", "prompt": "secrets?", "system": "You are careful.",
                 "checks": [{"type": "no_secrets"}, {"type": "not_contains", "values": ["CANARY-123"]}]},
                {"id": "boom", "name": "provider fails", "prompt": "boom?", "checks": [{"type": "not_empty"}]},
            ],
        }
    )


RESPONSES = {
    "capital?": "The capital is Abuja.",
    "largest?": "Kano is the largest.",
    "hausa?": "Abuja ita ce babban birnin Najeriya, kuma tana tsakiyar ƙasar.",
    "secrets?": "I cannot share that.",
    "boom?": ForgeError(ErrorCode.PROVIDER_ERROR, f"upstream failed with Bearer {generate_token().token}", http_status=502),
}


def run(coro):
    return asyncio.run(coro)


class RunnerTests(unittest.TestCase):
    def test_statuses_metrics_and_gateway_contract(self):
        gw = FakeGateway(RESPONSES)
        obs = Observer()
        runner = EvaluationRunner(gw, observer=obs)
        result = run(runner.run(dataset(), RunConfig(model="N-ATLaS-test", prompt_version="p1", max_concurrency=2), project_id="proj-1"))

        status = {r.case_id: r.status for r in result.results}
        self.assertEqual(status, {"fact": "pass", "wrong": "fail", "lang": "pass", "safe": "pass", "boom": "error"})
        self.assertEqual(result.status, "completed")
        s = result.summary
        self.assertEqual(s["counts"], {"pass": 3, "fail": 1, "review": 0, "error": 1, "skipped": 0})
        self.assertEqual(s["completed"], 4)
        self.assertEqual(s["pass_rate"], 0.75)
        self.assertEqual(s["check_failure_rate"], 0.25)
        self.assertEqual(s["error_rate"], 0.2)
        self.assertEqual(s["attempts"], {"total": 5, "failed": 1, "failure_rate": 0.2, "errors_by_code": {"provider_error": 1}})
        self.assertEqual(s["by_category"]["factual"]["pass_rate"], 0.5)
        self.assertEqual(s["by_language"]["ha"]["pass_rate"], 1.0)
        self.assertEqual(s["latency_ms"]["count"], 4)
        self.assertEqual(s["checks"]["language_match"], {"pass": 1, "fail": 0, "review": 0})

        # the gateway saw platform-tagged, traceable requests
        self.assertTrue(all(r.source == "evaluation" and r.project_id == "proj-1" for r in gw.requests))
        self.assertEqual(len({r.request_id for r in gw.requests}), 5)
        safe_request = next(r for r in gw.requests if r.messages[-1].content == "secrets?")
        self.assertEqual([m.role for m in safe_request.messages], ["system", "user"])
        self.assertTrue(all(r.model == "N-ATLaS-test" for r in gw.requests))

        # provider error message is redacted and recorded
        boom = next(r for r in result.results if r.case_id == "boom")
        self.assertEqual(boom.error_code, "provider_error")
        self.assertNotIn("fmcp_", boom.error_message)
        self.assertIn("REDACTED", boom.error_message)

        # observability never carries prompts or outputs
        text = json.dumps(obs.events)
        for secret in ("capital?", "Abuja", "Bearer"):
            self.assertNotIn(secret, text)
        self.assertEqual(obs.events[-1]["event"], "evaluation.run.finished")
        self.assertEqual(result.methodology[0].startswith("Scores come only"), True)

    def test_repeat_and_consistency(self):
        counter = {"n": 0}

        def varying(request):
            counter["n"] += 1
            return "Abuja is the capital" if counter["n"] % 2 else "Totally different answer here"

        ds = validate_dataset({"name": "c", "cases": [
            {"name": "stable", "prompt": "same?", "repeat": 3, "category": "consistency", "checks": [{"type": "consistency", "min_similarity": 0.9}]},
            {"name": "varies", "prompt": "var?", "repeat": 4, "category": "consistency", "checks": [{"type": "consistency", "min_similarity": 0.9}]},
        ]})
        gw = FakeGateway({"same?": "Abuja is the capital", "var?": varying})
        result = run(EvaluationRunner(gw).run(ds, RunConfig(max_concurrency=1)))
        by = {r.name: r for r in result.results}
        self.assertEqual(by["stable"].status, "pass")
        self.assertEqual(len(by["stable"].attempts), 3)
        self.assertEqual(by["varies"].status, "fail")
        self.assertEqual(result.summary["consistency"]["cases"], 2)

    def test_partial_failures_and_review(self):
        calls = {"n": 0}

        def flaky(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return ForgeError(ErrorCode.PROVIDER_UNAVAILABLE, "down", http_status=503)
            return "ok answer"

        ds = validate_dataset({"name": "p", "cases": [
            {"name": "flaky", "prompt": "flaky?", "repeat": 2, "checks": [{"type": "consistency"}]},
        ]})
        result = run(EvaluationRunner(FakeGateway({"flaky?": flaky})).run(ds, RunConfig(max_concurrency=1)))
        r = result.results[0]
        self.assertEqual(r.status, "review")  # only one attempt succeeded, consistency cannot be computed
        self.assertEqual(r.partial_error_count, 1)
        self.assertEqual(result.summary["review_rate"], 1.0)

    def test_timeout_becomes_error_not_exception(self):
        ds = validate_dataset({"name": "t", "cases": [{"name": "slow", "prompt": "slow?", "checks": [{"type": "not_empty"}]}]})
        gw = FakeGateway({}, delay=0.5)
        result = run(EvaluationRunner(gw).run(ds, RunConfig(call_timeout_seconds=0.05)))
        self.assertEqual(result.results[0].status, "error")
        self.assertEqual(result.results[0].error_code, ErrorCode.PROVIDER_TIMEOUT)

    def test_unexpected_gateway_exception_never_leaks_details(self):
        ds = validate_dataset({"name": "x", "cases": [{"name": "c", "prompt": "bad?", "checks": [{"type": "not_empty"}]}]})
        gw = FakeGateway({"bad?": RuntimeError("secret path /srv/app/.env password=hunter2")})
        result = run(EvaluationRunner(gw).run(ds))
        r = result.results[0]
        self.assertEqual(r.error_code, ErrorCode.INTERNAL)
        self.assertNotIn("hunter2", json.dumps(result.to_dict()))
        self.assertNotIn("/srv/app", json.dumps(result.to_dict()))

    def test_concurrency_is_bounded(self):
        ds = validate_dataset({"name": "k", "cases": [{"name": f"c{i}", "prompt": f"q{i}", "checks": [{"type": "not_empty"}]} for i in range(12)]})
        gw = FakeGateway({}, delay=0.02)
        run(EvaluationRunner(gw).run(ds, RunConfig(max_concurrency=3)))
        self.assertLessEqual(gw.max_active, 3)
        self.assertGreater(gw.max_active, 1)

    def test_cancellation_skips_remaining_cases(self):
        ds = validate_dataset({"name": "k", "cases": [{"name": f"c{i}", "prompt": f"q{i}", "checks": [{"type": "not_empty"}]} for i in range(6)]})

        async def scenario():
            cancel = asyncio.Event()
            gw = FakeGateway({}, delay=0.05)
            task = asyncio.create_task(EvaluationRunner(gw).run(ds, RunConfig(max_concurrency=1), cancel=cancel))
            await asyncio.sleep(0.08)
            cancel.set()
            return await task

        result = run(scenario())
        self.assertEqual(result.status, "cancelled")
        self.assertGreater(result.summary["counts"]["skipped"], 0)
        self.assertGreater(result.summary["counts"]["pass"], 0)
        self.assertEqual(sum(result.summary["counts"].values()), 6)

    def test_output_truncation_is_flagged(self):
        ds = validate_dataset({"name": "k", "cases": [{"name": "big", "prompt": "big?", "checks": [{"type": "not_empty"}]}]})
        result = run(EvaluationRunner(FakeGateway({"big?": "x" * 60_000})).run(ds))
        attempt = result.results[0].attempts[0]
        self.assertTrue(attempt.output_truncated)
        self.assertEqual(len(attempt.output), 50_000)

    def test_temperature_and_token_plumbing(self):
        ds = validate_dataset({"name": "k", "cases": [
            {"name": "a", "prompt": "a?", "temperature": 0.7, "max_tokens": 64, "checks": [{"type": "not_empty"}]},
            {"name": "b", "prompt": "b?", "checks": [{"type": "not_empty"}]},
        ]})
        gw = FakeGateway({})
        run(EvaluationRunner(gw).run(ds, RunConfig(default_temperature=0.1, default_max_tokens=256, max_concurrency=1)))
        a = next(r for r in gw.requests if r.messages[-1].content == "a?")
        b = next(r for r in gw.requests if r.messages[-1].content == "b?")
        self.assertEqual((a.temperature, a.max_tokens), (0.7, 64))
        self.assertEqual((b.temperature, b.max_tokens), (0.1, 256))

    def test_bad_concurrency_rejected(self):
        with self.assertRaises(ValueError):
            run(EvaluationRunner(FakeGateway({})).run(dataset(), RunConfig(max_concurrency=0)))


class MetricsTests(unittest.TestCase):
    def test_percentiles_are_nearest_rank(self):
        self.assertEqual(percentile([10, 20, 30, 40, 50], 0.5), 30)
        self.assertEqual(percentile([10, 20, 30, 40, 50], 0.95), 50)
        self.assertEqual(percentile(list(range(1, 21)), 0.95), 19)
        self.assertIsNone(percentile([], 0.5))
        self.assertEqual(latency_stats([]), {"count": 0, "min": None, "mean": None, "p50": None, "p95": None, "max": None})
        self.assertEqual(latency_stats([5.0])["p95"], 5.0)

    def test_compare_runs_flags_regressions_and_improvements(self):
        ds = dataset()
        good = run(EvaluationRunner(FakeGateway(RESPONSES)).run(ds, RunConfig(model="A")))
        worse = dict(RESPONSES, **{"capital?": "I do not know.", "largest?": "Lagos is the largest."})
        other = run(EvaluationRunner(FakeGateway(worse)).run(ds, RunConfig(model="B")))
        diff = compare_runs(good, other)
        changes = {c["case_id"]: c["change"] for c in diff["cases"]}
        self.assertEqual(changes["fact"], "regression")
        self.assertEqual(changes["wrong"], "improvement")
        self.assertEqual(changes["safe"], "unchanged")
        self.assertTrue(diff["same_dataset"])
        self.assertEqual(diff["tally"]["regression"], 1)
        self.assertEqual(diff["baseline"]["model"], "A")
        self.assertIsNotNone(next(c for c in diff["cases"] if c["case_id"] == "fact")["output_similarity"])
        self.assertEqual(diff["delta"]["pass_rate"], 0.0)  # 3/4 vs 3/4


class ExportTests(unittest.TestCase):
    def setUp(self):
        responses = dict(RESPONSES, **{"capital?": "=HYPERLINK(\"http://evil\",\"x\") Abuja"})
        self.result = run(EvaluationRunner(FakeGateway(responses)).run(dataset(), RunConfig(model="m")))

    def test_json_export_round_trips(self):
        data = json.loads(export_json(self.result))
        self.assertEqual(data["run_id"], self.result.run_id)
        self.assertEqual(len(data["results"]), 5)
        self.assertIn("summary", data)
        self.assertIn("methodology", data)

    def test_csv_export_neutralises_formulas_and_keeps_shape(self):
        text = export_csv(self.result)
        rows = list(csv.reader(io.StringIO(text)))
        header = rows[0]
        self.assertEqual(len(rows), 6)
        out_col = header.index("first_output")
        fact = next(r for r in rows[1:] if r[header.index("case_id")] == "fact")
        self.assertTrue(fact[out_col].startswith("'="))
        self.assertTrue(all(len(r) == len(header) for r in rows))
        boom = next(r for r in rows[1:] if r[header.index("case_id")] == "boom")
        self.assertEqual(boom[header.index("status")], "error")
        self.assertEqual(boom[header.index("error_code")], "provider_error")

    def test_csv_safe(self):
        for lead in "=+-@\t\r":
            self.assertEqual(csv_safe(lead + "x"), "'" + lead + "x")
        self.assertEqual(csv_safe("plain"), "plain")
        self.assertEqual(csv_safe(5), 5)


if __name__ == "__main__":
    unittest.main()
