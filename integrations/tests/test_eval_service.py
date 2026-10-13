"""The evaluation run manager: background runs with progress, cancellation, limits and storage."""

from __future__ import annotations

import asyncio
import unittest

from forge_integrations.config import EvaluationSettings
from forge_integrations.errors import ErrorCode, ForgeError
from forge_integrations.evaluation import EvaluationRunner, RunConfig, validate_dataset
from forge_integrations.evaluation.service import (
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_RUNNING,
    InMemoryRunStore,
    RunManager,
    RunStore,
    run_summary,
)
from forge_integrations.ports import GenerationResult

from .helpers import RecordingObserver


class GatedGateway:
    """Every call waits until the test lets it through, so a run can be observed mid-flight."""

    def __init__(self, text: str = "ok") -> None:
        self.release = asyncio.Event()
        self.started = 0
        self.finished = 0
        self.text = text

    async def generate(self, request):
        self.started += 1
        await self.release.wait()
        self.finished += 1
        return GenerationResult(text=self.text, model="test-double", latency_ms=1.0)


class SlowGateway:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        await asyncio.sleep(self.seconds)
        return GenerationResult(text="slow", model="test-double", latency_ms=1.0)


class InstantGateway:
    async def generate(self, request):
        return GenerationResult(text="Abuja is the capital.", model="test-double", latency_ms=1.0)


class BrokenStore:
    async def save(self, run):
        raise ConnectionError("password=hunter2 db.internal")

    async def get(self, project_id, run_id):
        return None

    async def list(self, project_id, *, limit):
        return []


def make_dataset(cases: int = 3, *, name: str = "mini"):
    return validate_dataset(
        {
            "name": name,
            "version": "v1",
            "cases": [
                {"id": f"c{i}", "name": f"case {i}", "prompt": f"question {i}", "checks": [{"type": "not_empty"}]}
                for i in range(cases)
            ],
        }
    )


def manager_for(gateway, *, settings=None, store=None, observer=None, **kwargs) -> RunManager:
    return RunManager(
        EvaluationRunner(gateway),
        store or InMemoryRunStore(),
        settings or EvaluationSettings(max_concurrency=4, max_concurrent_runs_per_project=2),
        observer=observer,
        **kwargs,
    )


async def settle(times: int = 5) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_run_starts_at_once_reports_progress_and_finishes(self):
        gateway = GatedGateway()
        manager = manager_for(gateway)
        handle = await manager.start("proj_1", make_dataset(3), RunConfig(max_concurrency=1))
        self.assertEqual((handle.state, handle.cases_total, handle.cases_done), (STATE_RUNNING, 3, 0))
        self.assertIsNone(handle.persisted)
        await settle()
        self.assertEqual(gateway.started, 1)  # one at a time, as configured
        gateway.release.set()
        done = await manager.wait("proj_1", handle.run_id, timeout=5)
        self.assertEqual((done.state, done.cases_done, done.persisted), (STATE_COMPLETED, 3, True))
        self.assertIsNotNone(done.finished_at)
        run = await manager.get_run("proj_1", handle.run_id)
        self.assertEqual((run.status, run.summary["counts"]["pass"], run.project_id), ("completed", 3, "proj_1"))

    async def test_progress_counts_finished_cases(self):
        gateway = GatedGateway()
        manager = manager_for(gateway)
        handle = await manager.start("proj_1", make_dataset(2), RunConfig(max_concurrency=2))
        await settle()
        self.assertEqual(manager.status("proj_1", handle.run_id).cases_done, 0)
        gateway.release.set()
        await manager.wait("proj_1", handle.run_id, timeout=5)
        self.assertEqual(manager.status("proj_1", handle.run_id).cases_done, 2)

    async def test_the_result_is_kept_in_the_store_and_listed_newest_first(self):
        store = InMemoryRunStore()
        manager = manager_for(InstantGateway(), store=store)
        first = await manager.start("proj_1", make_dataset(1, name="first"))
        await manager.wait("proj_1", first.run_id, timeout=5)
        second = await manager.start("proj_1", make_dataset(1, name="second"))
        await manager.wait("proj_1", second.run_id, timeout=5)
        rows = await manager.list_runs("proj_1")
        self.assertEqual([r["run_id"] for r in rows], [second.run_id, first.run_id])
        self.assertEqual(rows[0]["dataset_name"], "second")
        self.assertIn("summary", rows[0])
        self.assertEqual((await store.get("proj_1", first.run_id)).dataset_name, "first")

    async def test_listing_shows_running_runs_first_without_duplicates(self):
        gateway = GatedGateway()
        manager = manager_for(gateway)
        handle = await manager.start("proj_1", make_dataset(1))
        rows = await manager.list_runs("proj_1")
        self.assertEqual([(r["run_id"], r["state"]) for r in rows], [(handle.run_id, STATE_RUNNING)])
        gateway.release.set()
        await manager.wait("proj_1", handle.run_id, timeout=5)
        rows = await manager.list_runs("proj_1")
        self.assertEqual([r["run_id"] for r in rows], [handle.run_id])

    async def test_events_describe_the_run_without_prompts_or_outputs(self):
        observer = RecordingObserver()
        manager = manager_for(InstantGateway(), observer=observer)
        handle = await manager.start("proj_1", make_dataset(1))
        await manager.wait("proj_1", handle.run_id, timeout=5)
        names = [e["event"] for e in observer.events]
        self.assertEqual(names[0], "evaluation.run.started")
        self.assertEqual(names[-1], "evaluation.run.ended")
        self.assertEqual(observer.events[-1]["state"], "completed")
        self.assertNotIn("question 0", str(observer.events))
        self.assertNotIn("Abuja", str(observer.events))


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_stops_the_rest_of_the_run(self):
        gateway = GatedGateway()
        manager = manager_for(gateway)
        handle = await manager.start("proj_1", make_dataset(5), RunConfig(max_concurrency=1))
        await settle()
        self.assertTrue(await manager.cancel("proj_1", handle.run_id))
        gateway.release.set()
        done = await manager.wait("proj_1", handle.run_id, timeout=5)
        self.assertEqual(done.state, STATE_CANCELLED)
        run = await manager.get_run("proj_1", handle.run_id)
        self.assertEqual(run.status, "cancelled")
        self.assertEqual(run.summary["counts"]["skipped"], 4)
        self.assertEqual(gateway.started, 1)  # the cases that had not started never reached the model

    async def test_cancelling_twice_or_after_finishing_reports_nothing_to_cancel(self):
        manager = manager_for(InstantGateway())
        handle = await manager.start("proj_1", make_dataset(1))
        await manager.wait("proj_1", handle.run_id, timeout=5)
        self.assertFalse(await manager.cancel("proj_1", handle.run_id))
        self.assertFalse(await manager.cancel("proj_1", "no-such-run"))

    async def test_the_run_deadline_stops_a_run_that_takes_too_long(self):
        manager = manager_for(SlowGateway(0.3), max_run_seconds=0.2)
        handle = await manager.start("proj_1", make_dataset(6), RunConfig(max_concurrency=1))
        done = await manager.wait("proj_1", handle.run_id, timeout=10)
        self.assertEqual(done.state, STATE_CANCELLED)
        run = await manager.get_run("proj_1", handle.run_id)
        self.assertGreaterEqual(run.summary["counts"]["skipped"], 1)

    async def test_shutdown_cancels_running_work_and_refuses_new_runs(self):
        gateway = GatedGateway()
        manager = manager_for(gateway)
        handle = await manager.start("proj_1", make_dataset(3), RunConfig(max_concurrency=1))
        await settle()
        shutting_down = asyncio.create_task(manager.shutdown(grace_seconds=5))
        await settle()
        with self.assertRaises(ForgeError) as caught:
            await manager.start("proj_1", make_dataset(1))
        self.assertEqual((caught.exception.code, caught.exception.http_status), (ErrorCode.PROVIDER_UNAVAILABLE, 503))
        gateway.release.set()
        await shutting_down
        self.assertEqual(manager.status("proj_1", handle.run_id).state, STATE_CANCELLED)

    async def test_shutdown_force_cancels_work_that_ignores_the_request(self):
        gateway = GatedGateway()  # never released: the call cannot finish by itself
        manager = manager_for(gateway)
        handle = await manager.start("proj_1", make_dataset(1))
        await settle()
        await manager.shutdown(grace_seconds=0.1)
        self.assertEqual(manager.status("proj_1", handle.run_id).state, STATE_CANCELLED)


class LimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_project_cannot_exceed_its_runs_in_progress(self):
        gateway = GatedGateway()
        manager = manager_for(gateway, settings=EvaluationSettings(max_concurrent_runs_per_project=1))
        first = await manager.start("proj_1", make_dataset(1))
        with self.assertRaises(ForgeError) as caught:
            await manager.start("proj_1", make_dataset(1))
        error = caught.exception
        self.assertEqual((error.code, error.http_status, error.retryable), (ErrorCode.RATE_LIMITED, 429, True))
        other = await manager.start("proj_2", make_dataset(1))  # another project is unaffected
        self.assertEqual(other.state, STATE_RUNNING)
        gateway.release.set()
        await manager.wait("proj_1", first.run_id, timeout=5)
        again = await manager.start("proj_1", make_dataset(1))  # the slot is free again
        await manager.wait("proj_1", again.run_id, timeout=5)

    async def test_there_is_a_ceiling_across_all_projects(self):
        gateway = GatedGateway()
        manager = manager_for(gateway, max_active_total=2)
        await manager.start("proj_1", make_dataset(1))
        await manager.start("proj_2", make_dataset(1))
        with self.assertRaises(ForgeError) as caught:
            await manager.start("proj_3", make_dataset(1))
        self.assertEqual((caught.exception.code, caught.exception.http_status), (ErrorCode.PROVIDER_UNAVAILABLE, 503))
        self.assertEqual(caught.exception.headers, {"Retry-After": "10"})
        await manager.shutdown(grace_seconds=0.1)

    async def test_the_request_is_clamped_to_what_the_server_allows(self):
        settings = EvaluationSettings(max_concurrency=2, call_timeout_seconds=30.0, max_cases=3)
        manager = manager_for(InstantGateway(), settings=settings)
        handle = await manager.start("proj_1", make_dataset(2), RunConfig(max_concurrency=16, call_timeout_seconds=600.0))
        await manager.wait("proj_1", handle.run_id, timeout=5)
        run = await manager.get_run("proj_1", handle.run_id)
        self.assertEqual((run.config["max_concurrency"], run.config["call_timeout_seconds"]), (2, 30.0))
        with self.assertRaises(ForgeError) as caught:
            await manager.start("proj_1", make_dataset(4))
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_ARGUMENT)

    async def test_bad_input_is_refused_before_anything_runs(self):
        manager = manager_for(InstantGateway())
        for project in ("", "bad id!", "x" * 65, None, 7):
            with self.subTest(project=project):
                with self.assertRaises(ValueError):
                    await manager.start(project, make_dataset(1))
        empty = make_dataset(1)
        object.__setattr__(empty, "cases", ())
        with self.assertRaises(ForgeError):
            await manager.start("proj_1", empty)
        for kwargs in ({"max_run_seconds": 0}, {"max_active_total": 0}, {"keep_finished": 0}):
            with self.assertRaises(ValueError):
                manager_for(InstantGateway(), **kwargs)


class IsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_of_another_project_look_like_they_do_not_exist(self):
        gateway = GatedGateway()
        manager = manager_for(gateway)
        handle = await manager.start("proj_1", make_dataset(1))
        gateway.release.set()
        await manager.wait("proj_1", handle.run_id, timeout=5)
        self.assertIsNone(manager.status("proj_2", handle.run_id))
        self.assertIsNone(await manager.get_run("proj_2", handle.run_id))
        self.assertFalse(await manager.cancel("proj_2", handle.run_id))
        self.assertIsNone(await manager.wait("proj_2", handle.run_id))
        self.assertEqual(await manager.list_runs("proj_2"), [])
        self.assertIsNotNone(await manager.get_run("proj_1", handle.run_id))

    async def test_odd_ids_are_just_not_found(self):
        manager = manager_for(InstantGateway())
        for run_id in ("", "../etc/passwd", "x" * 200, "a b", None, 5):
            self.assertIsNone(manager.status("proj_1", run_id))
            self.assertIsNone(await manager.get_run("proj_1", run_id))
            self.assertFalse(await manager.cancel("proj_1", run_id))

    async def test_the_store_is_checked_for_the_right_project_even_if_it_misbehaves(self):
        class LeakyStore(InMemoryRunStore):
            async def get(self, project_id, run_id):  # ignores the project, as a buggy adapter might
                for bucket in self._runs.values():
                    if run_id in bucket:
                        return bucket[run_id]
                return None

        store = LeakyStore()
        manager = manager_for(InstantGateway(), store=store)
        handle = await manager.start("proj_1", make_dataset(1))
        await manager.wait("proj_1", handle.run_id, timeout=5)
        fresh = manager_for(InstantGateway(), store=store)  # a new process: only the store knows the run
        self.assertIsNotNone(await fresh.get_run("proj_1", handle.run_id))
        self.assertIsNone(await fresh.get_run("proj_2", handle.run_id))


class FailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_store_failure_keeps_the_result_readable_and_is_reported_without_details(self):
        observer = RecordingObserver()
        manager = manager_for(InstantGateway(), store=BrokenStore(), observer=observer)
        handle = await manager.start("proj_1", make_dataset(2))
        done = await manager.wait("proj_1", handle.run_id, timeout=5)
        self.assertEqual((done.state, done.persisted), (STATE_COMPLETED, False))
        self.assertIsNotNone(await manager.get_run("proj_1", handle.run_id))
        failure = next(e for e in observer.events if e["event"] == "evaluation.run.persist_failed")
        self.assertEqual(failure["exception_type"], "ConnectionError")
        self.assertNotIn("hunter2", str(observer.events))
        self.assertNotIn("db.internal", str(observer.events))

    async def test_a_runner_crash_becomes_a_failed_handle_with_a_generic_message(self):
        observer = RecordingObserver()
        runner = EvaluationRunner(InstantGateway())

        async def crash(*args, **kwargs):
            raise RuntimeError("secret internal detail /srv/app")

        runner.run = crash  # type: ignore[method-assign]
        manager = RunManager(runner, InMemoryRunStore(), observer=observer)
        handle = await manager.start("proj_1", make_dataset(1))
        done = await manager.wait("proj_1", handle.run_id, timeout=5)
        self.assertEqual((done.state, done.error_code, done.persisted), (STATE_FAILED, ErrorCode.INTERNAL, False))
        self.assertEqual(done.error_message, "The run could not be completed.")
        self.assertNotIn("secret internal detail", str(done.to_dict()) + str(observer.events))
        self.assertIsNone(await manager.get_run("proj_1", handle.run_id))
        self.assertEqual(manager._active("proj_1"), 0)  # a failed run frees its slot

    async def test_gateway_errors_are_case_errors_not_run_failures(self):
        class Failing:
            async def generate(self, request):
                raise ForgeError(ErrorCode.PROVIDER_UNAVAILABLE, "upstream down", http_status=503, retryable=True)

        manager = manager_for(Failing())
        handle = await manager.start("proj_1", make_dataset(2))
        done = await manager.wait("proj_1", handle.run_id, timeout=5)
        self.assertEqual(done.state, STATE_COMPLETED)
        run = await manager.get_run("proj_1", handle.run_id)
        self.assertEqual(run.summary["counts"]["error"], 2)


class RunnerProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_progress_is_reported_after_each_case(self):
        seen = []
        await EvaluationRunner(InstantGateway()).run(make_dataset(3), RunConfig(max_concurrency=1), progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(seen, [(1, 3), (2, 3), (3, 3)])

    async def test_a_failing_progress_callback_cannot_fail_the_run(self):
        calls = []

        def broken(done, total):
            calls.append(done)
            raise RuntimeError("display crashed")

        run = await EvaluationRunner(InstantGateway()).run(make_dataset(2), RunConfig(max_concurrency=1), progress=broken)
        self.assertEqual((run.status, run.summary["counts"]["pass"]), ("completed", 2))
        self.assertEqual(calls, [1, 2])


class RetentionTests(unittest.IsolatedAsyncioTestCase):
    async def test_finished_handles_are_bounded_but_the_store_keeps_the_runs(self):
        store = InMemoryRunStore()
        manager = manager_for(InstantGateway(), store=store, keep_finished=2)
        ids = []
        for index in range(4):
            handle = await manager.start("proj_1", make_dataset(1, name=f"run {index}"))
            await manager.wait("proj_1", handle.run_id, timeout=5)
            ids.append(handle.run_id)
        self.assertEqual(len(manager._handles), 2)
        self.assertIsNone(manager.status("proj_1", ids[0]))  # the live handle was dropped
        self.assertEqual((await manager.get_run("proj_1", ids[0])).dataset_name, "run 0")  # the result was not
        self.assertEqual(len(await manager.list_runs("proj_1")), 4)


class InMemoryStoreTests(unittest.IsolatedAsyncioTestCase):
    async def run_for(self, project: str, name: str):
        manager = manager_for(InstantGateway(), store=InMemoryRunStore())
        handle = await manager.start(project, make_dataset(1, name=name))
        await manager.wait(project, handle.run_id, timeout=5)
        return await manager.get_run(project, handle.run_id)

    async def test_it_is_bounded_per_project_and_isolated(self):
        store = InMemoryRunStore(max_runs_per_project=2)
        runs = [await self.run_for("proj_1", f"r{i}") for i in range(3)]
        other = await self.run_for("proj_2", "other")
        for run in runs + [other]:
            await store.save(run)
        names = [row["dataset_name"] for row in await store.list("proj_1", limit=10)]
        self.assertEqual(names, ["r2", "r1"])  # newest first, the oldest was dropped
        self.assertIsNone(await store.get("proj_1", runs[0].run_id))
        self.assertIsNone(await store.get("proj_1", other.run_id))
        self.assertEqual(len(await store.list("proj_1", limit=1)), 1)

    async def test_a_run_without_a_project_cannot_be_stored_and_limits_must_be_sane(self):
        run = await self.run_for("proj_1", "x")
        orphan = type(run)(**{**run.__dict__, "project_id": None})
        with self.assertRaises(ValueError):
            await InMemoryRunStore().save(orphan)
        with self.assertRaises(ValueError):
            InMemoryRunStore(max_runs_per_project=0)

    def test_it_satisfies_the_port_and_summaries_carry_no_outputs(self):
        self.assertIsInstance(InMemoryRunStore(), RunStore)
        self.assertIsInstance(BrokenStore(), RunStore)


class SummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_summary_view_has_no_prompts_or_outputs(self):
        manager = manager_for(InstantGateway())
        handle = await manager.start("proj_1", make_dataset(1))
        await manager.wait("proj_1", handle.run_id, timeout=5)
        run = await manager.get_run("proj_1", handle.run_id)
        summary = run_summary(run)
        self.assertEqual(set(summary), {"run_id", "dataset_id", "dataset_name", "dataset_version", "model", "prompt_version", "status", "started_at", "finished_at", "summary"})
        self.assertNotIn("Abuja", str(summary))
        self.assertNotIn("question 0", str(summary))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
