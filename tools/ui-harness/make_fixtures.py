"""Builds the fixtures the browser tests use, from the real evaluation engine.

Run it once before the tests: python3 tools/ui-harness/make_fixtures.py
It writes dist/fixtures/runs.json (a completed run, a run where most cases hit model errors, and a
genuinely cancelled run) and dist/fixtures/catalogue.json (the starters and the check catalogue,
exactly as the API serves them). They go in dist/, the ignored folder for generated output, so they are never committed.
"""
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "integrations" / "src"))

from forge_integrations.config import EvaluationSettings  # noqa: E402
from forge_integrations.errors import ForgeError  # noqa: E402
from forge_integrations.evaluation import describe_starter_datasets, load_starter_dataset  # noqa: E402
from forge_integrations.evaluation.catalogue import check_catalogue  # noqa: E402
from forge_integrations.evaluation.models import RunConfig  # noqa: E402
from forge_integrations.evaluation.runner import EvaluationRunner  # noqa: E402
from forge_integrations.evaluation.service import InMemoryRunStore, RunManager  # noqa: E402
from forge_integrations.evaluation.validation import validate_dataset  # noqa: E402
from forge_integrations.ports import GenerationResult, Usage  # noqa: E402

PROJECT = "11111111-1111-4111-8111-111111111111"


class Mixed:
    """Plausible replies: some right, some wrong, some in the wrong language."""

    async def generate(self, request):
        prompt = request.messages[-1].content
        await asyncio.sleep(0.004 + (len(prompt) % 7) * 0.004)  # so measured latencies are not zero
        text = {
            "yo": "Ẹ kú àárọ̀! Mo wà dáadáa, ẹ ṣé.",
            "ha": "Barka da safiya! Lafiya lau.",
            "ig": "Ụtụtụ ọma! Adị m mma.",
        }.get(request.language or "", "Hello! The answer is 42 and this is a short reply.")
        return GenerationResult(
            text=text,
            model="NCAIR1/N-ATLaS",
            usage=Usage(14, 11, 25),
            latency_ms=380.0 + len(prompt),
            finish_reason="stop",
        )


class Flaky:
    async def generate(self, request):
        size = len(request.messages[-1].content) % 3
        if size == 0:
            raise ForgeError("provider_timeout", "The model did not answer in time.", http_status=504, retryable=True)
        if size == 1:
            raise ForgeError("provider_unavailable", "The N-ATLAS inference service is not reachable.", http_status=503, retryable=True)
        return GenerationResult(text="Fine.", model="NCAIR1/N-ATLaS", usage=Usage(5, 2, 7), latency_ms=200.0, finish_reason="stop")


class Slow:
    async def generate(self, request):
        await asyncio.sleep(0.3)
        return GenerationResult(text="Okay", model="NCAIR1/N-ATLaS", usage=Usage(5, 2, 7), latency_ms=300.0, finish_reason="stop")


def settings():
    return EvaluationSettings(max_cases=100, max_concurrency=2, call_timeout_seconds=10.0, max_concurrent_runs_per_project=1)


async def run_with(gateway, dataset, label, cancel_after=None):
    manager = RunManager(EvaluationRunner(gateway), InMemoryRunStore(), settings())
    handle = await manager.start(PROJECT, dataset, RunConfig(model="NCAIR1/N-ATLaS", prompt_version=label, max_concurrency=2))
    if cancel_after is not None:
        await asyncio.sleep(cancel_after)
        await manager.cancel(PROJECT, handle.run_id)
    while manager.status(PROJECT, handle.run_id).state == "running":
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    run = await manager.get_run(PROJECT, handle.run_id)
    await manager.shutdown()
    return run.to_dict()


async def main():
    languages = load_starter_dataset("starter-nigerian-languages")
    six = validate_dataset(
        {
            "name": "Six slow cases",
            "cases": [
                {"id": f"c{i}", "name": f"Case {i}", "category": "general", "prompt": f"Prompt number {i}", "checks": [{"type": "not_empty"}]}
                for i in range(6)
            ],
        }
    )
    runs = {
        "completed": await run_with(Mixed(), languages, "v3"),
        "errors": await run_with(Flaky(), load_starter_dataset("starter-general-checks"), "v1"),
        "cancelled": await run_with(Slow(), six, "v2", cancel_after=0.15),
    }
    for name, document in runs.items():
        document["project_id"] = PROJECT
        print(name, document["status"], document["summary"]["counts"])
    fixtures = HERE / "dist" / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    (fixtures / "runs.json").write_text(json.dumps(runs, ensure_ascii=False), encoding="utf-8")
    langs_dataset = {
        "cases": [
            {"id": c.id, "name": c.name, "category": c.category, "language": c.language, "prompt": c.prompt, "checks": [ch.to_dict() for ch in c.checks]}
            for c in languages.cases
        ]
    }
    (fixtures / "catalogue.json").write_text(
        json.dumps({"starters": describe_starter_datasets(), "checks": check_catalogue(), "langs_dataset": langs_dataset}, ensure_ascii=False),
        encoding="utf-8",
    )


asyncio.run(main())
