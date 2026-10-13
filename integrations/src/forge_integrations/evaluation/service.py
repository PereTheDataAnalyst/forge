"""Running evaluations in the background: start, watch, cancel and keep the results.

``RunManager`` is the layer a web backend calls. A route that starts a run returns at once with a
``RunHandle``; the dashboard polls ``status`` for progress and ``get_run`` for the finished result.

Design rules:

  * One event loop owns a manager. Nothing here is thread safe, on purpose.
  * Every lookup takes the project id, and a run of another project looks exactly like a run that
    does not exist, so ids cannot be probed across projects.
  * Limits are enforced where runs start: runs in progress per project and in total, and a hard
    deadline per run. A project that is over its limit gets a retryable ``rate_limited`` error.
  * Failures never escape as exceptions or tracebacks. A run that cannot finish becomes a handle in
    state ``failed`` with a generic message; a result that cannot be saved stays readable from
    memory and the failure is reported through the observer.
  * Persistence is a port (``RunStore``). The platform supplies one backed by its database; the
    in-memory store here is for tests, demos and single-process use.
"""

from __future__ import annotations

import asyncio
import re
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol, runtime_checkable
from uuid import uuid4

from ..config import EvaluationSettings
from ..errors import ErrorCode, ForgeError
from ..observability import NullObserver
from ..ports import Observer
from .models import Dataset, Run, RunConfig
from .runner import EvaluationRunner

STATE_RUNNING = "running"
STATE_COMPLETED = "completed"
STATE_CANCELLED = "cancelled"
STATE_FAILED = "failed"
FINISHED_STATES = (STATE_COMPLETED, STATE_CANCELLED, STATE_FAILED)

_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def run_summary(run: Run) -> dict[str, Any]:
    """The small view of a run used in lists: identity, status and the metrics summary, no outputs."""
    return {
        "run_id": run.run_id,
        "dataset_id": run.dataset_id,
        "dataset_name": run.dataset_name,
        "dataset_version": run.dataset_version,
        "model": run.model,
        "prompt_version": run.prompt_version,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "summary": run.summary,
    }


@runtime_checkable
class RunStore(Protocol):
    """Where finished runs are kept. Every method is scoped to a project."""

    async def save(self, run: Run) -> None: ...

    async def get(self, project_id: str, run_id: str) -> Run | None: ...

    async def list(self, project_id: str, *, limit: int) -> list[dict[str, Any]]:
        """Summaries (see ``run_summary``), newest first."""
        ...


class InMemoryRunStore:
    """Bounded and not durable. Oldest runs of a project are dropped first."""

    def __init__(self, max_runs_per_project: int = 100) -> None:
        if max_runs_per_project < 1:
            raise ValueError("max_runs_per_project must be at least 1")
        self._max = max_runs_per_project
        self._runs: dict[str, OrderedDict[str, Run]] = defaultdict(OrderedDict)

    async def save(self, run: Run) -> None:
        if run.project_id is None:
            raise ValueError("a stored run needs a project id")
        bucket = self._runs[run.project_id]
        bucket[run.run_id] = run
        bucket.move_to_end(run.run_id)
        while len(bucket) > self._max:
            bucket.popitem(last=False)

    async def get(self, project_id: str, run_id: str) -> Run | None:
        return self._runs.get(project_id, {}).get(run_id)

    async def list(self, project_id: str, *, limit: int) -> list[dict[str, Any]]:
        runs = list(self._runs.get(project_id, {}).values())
        return [run_summary(run) for run in reversed(runs)][:limit]


@dataclass
class RunHandle:
    run_id: str
    project_id: str
    dataset_id: str
    dataset_name: str
    state: str
    cases_total: int
    cases_done: int
    created_at: str
    finished_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    persisted: bool | None = None  # None until the run has finished

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "dataset_id": self.dataset_id,
            "dataset_name": self.dataset_name,
            "state": self.state,
            "cases_total": self.cases_total,
            "cases_done": self.cases_done,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "persisted": self.persisted,
        }


class RunManager:
    def __init__(
        self,
        runner: EvaluationRunner,
        store: RunStore,
        settings: EvaluationSettings | None = None,
        *,
        observer: Observer | None = None,
        max_run_seconds: float = 900.0,
        max_active_total: int = 32,
        keep_finished: int = 200,
        clock: Callable[[], str] = _utc_now,
        new_id: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        if max_run_seconds <= 0 or max_active_total < 1 or keep_finished < 1:
            raise ValueError("max_run_seconds, max_active_total and keep_finished must be positive")
        self._runner = runner
        self._store = store
        self._settings = settings or EvaluationSettings()
        self._observer: Observer = observer or NullObserver()
        self._max_run_seconds = max_run_seconds
        self._max_active_total = max_active_total
        self._keep_finished = keep_finished
        self._clock = clock
        self._new_id = new_id
        self._handles: OrderedDict[str, RunHandle] = OrderedDict()
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._finished_runs: OrderedDict[str, Run] = OrderedDict()
        self._closed = False

    # -- helpers --------------------------------------------------------------------------
    def _emit(self, event: Mapping[str, Any]) -> None:
        try:
            self._observer.emit(event)
        except Exception:  # noqa: BLE001 - observability never breaks a run
            pass

    def _active(self, project_id: str | None = None) -> int:
        return sum(1 for h in self._handles.values() if h.state == STATE_RUNNING and (project_id is None or h.project_id == project_id))

    def _own(self, project_id: str, run_id: str) -> RunHandle | None:
        handle = self._handles.get(run_id) if isinstance(run_id, str) else None
        return handle if handle is not None and handle.project_id == project_id else None

    def _trim(self) -> None:
        finished = [rid for rid, h in self._handles.items() if h.state in FINISHED_STATES]
        for rid in finished[: max(0, len(finished) - self._keep_finished)]:
            self._handles.pop(rid, None)
            self._finished_runs.pop(rid, None)

    # -- start ----------------------------------------------------------------------------
    async def start(self, project_id: str, dataset: Dataset, config: RunConfig | None = None) -> RunHandle:
        if self._closed:
            raise ForgeError(ErrorCode.PROVIDER_UNAVAILABLE, "The evaluation service is shutting down. Retry shortly.", http_status=503, retryable=True)
        if not isinstance(project_id, str) or not _PROJECT_ID_RE.match(project_id):
            raise ValueError("project_id must be 1 to 64 characters: letters, digits and _ . : -")
        if not dataset.cases:
            raise ForgeError(ErrorCode.INVALID_ARGUMENT, "The dataset has no cases.", http_status=422)
        if len(dataset.cases) > self._settings.max_cases:
            raise ForgeError(
                ErrorCode.INVALID_ARGUMENT,
                f"The dataset has {len(dataset.cases)} cases and this server runs at most {self._settings.max_cases}.",
                http_status=422,
            )
        per_project = self._settings.max_concurrent_runs_per_project
        if self._active(project_id) >= per_project:
            raise ForgeError(
                ErrorCode.RATE_LIMITED,
                f"This project already has {per_project} evaluation run(s) in progress. Wait for one to finish or cancel it.",
                http_status=429,
                retryable=True,
            )
        if self._active() >= self._max_active_total:
            raise ForgeError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "The server is running as many evaluations as it can. Retry shortly.",
                http_status=503,
                retryable=True,
                headers={"Retry-After": "10"},
            )

        requested = config or RunConfig()
        effective = replace(
            requested,
            max_concurrency=max(1, min(requested.max_concurrency, self._settings.max_concurrency)),
            call_timeout_seconds=max(1.0, min(requested.call_timeout_seconds, self._settings.call_timeout_seconds)),
        )
        run_id = self._new_id()
        if not _RUN_ID_RE.match(run_id):
            raise ForgeError(ErrorCode.INTERNAL, "Could not allocate a run id.", http_status=500)
        handle = RunHandle(
            run_id=run_id,
            project_id=project_id,
            dataset_id=dataset.id,
            dataset_name=dataset.name,
            state=STATE_RUNNING,
            cases_total=len(dataset.cases),
            cases_done=0,
            created_at=self._clock(),
        )
        cancel = asyncio.Event()
        self._handles[run_id] = handle
        self._cancel_events[run_id] = cancel
        task = asyncio.create_task(self._execute(handle, dataset, effective, cancel), name=f"evaluation-run-{run_id[:8]}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t, rid=run_id: self._tasks.pop(rid, None))
        self._emit({"event": "evaluation.run.started", "run_id": run_id, "project_id": project_id, "dataset_id": dataset.id, "cases": len(dataset.cases)})
        return handle

    async def _execute(self, handle: RunHandle, dataset: Dataset, config: RunConfig, cancel: asyncio.Event) -> None:
        deadline = asyncio.get_running_loop().call_later(self._max_run_seconds, cancel.set)

        def progress(done: int, total: int) -> None:
            handle.cases_done = done

        run: Run | None = None
        try:
            run = await self._runner.run(
                dataset, config, project_id=handle.project_id, run_id=handle.run_id, cancel=cancel, progress=progress
            )
            handle.state = STATE_CANCELLED if run.status == "cancelled" else STATE_COMPLETED
        except asyncio.CancelledError:
            handle.state = STATE_CANCELLED
            handle.finished_at = self._clock()
            raise
        except Exception as exc:  # noqa: BLE001 - never leak internals into a handle
            handle.state = STATE_FAILED
            handle.error_code = ErrorCode.INTERNAL
            handle.error_message = "The run could not be completed."
            self._emit({"event": "evaluation.run.failed", "run_id": handle.run_id, "project_id": handle.project_id, "exception_type": type(exc).__name__})
        finally:
            deadline.cancel()
            self._cancel_events.pop(handle.run_id, None)
            if handle.finished_at is None:
                handle.finished_at = self._clock()

        if run is not None:
            handle.cases_done = handle.cases_total
            self._finished_runs[handle.run_id] = run
            try:
                await self._store.save(run)
                handle.persisted = True
            except Exception as exc:  # noqa: BLE001
                handle.persisted = False
                self._emit(
                    {"event": "evaluation.run.persist_failed", "run_id": handle.run_id, "project_id": handle.project_id, "exception_type": type(exc).__name__}
                )
        else:
            handle.persisted = False
        self._emit(
            {
                "event": "evaluation.run.ended",
                "run_id": handle.run_id,
                "project_id": handle.project_id,
                "state": handle.state,
                "persisted": handle.persisted,
            }
        )
        self._trim()

    # -- read -----------------------------------------------------------------------------
    def status(self, project_id: str, run_id: str) -> RunHandle | None:
        """Live state of a run started by this process. ``None`` for unknown runs and other projects' runs."""
        return self._own(project_id, run_id)

    async def get_run(self, project_id: str, run_id: str) -> Run | None:
        """The finished result, from memory or from the store."""
        if not isinstance(project_id, str) or not isinstance(run_id, str) or not _RUN_ID_RE.match(run_id):
            return None
        handle = self._handles.get(run_id)
        if handle is not None:
            if handle.project_id != project_id:
                return None
            cached = self._finished_runs.get(run_id)
            if cached is not None:
                return cached
        stored = await self._store.get(project_id, run_id)
        return stored if stored is not None and stored.project_id == project_id else None

    async def list_runs(self, project_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Runs in progress first, then stored runs, newest first, without duplicates."""
        limit = max(1, min(int(limit), 200))
        live = [h.to_dict() for h in reversed(self._handles.values()) if h.project_id == project_id and h.state == STATE_RUNNING]
        seen = {row["run_id"] for row in live}
        stored = [row for row in await self._store.list(project_id, limit=limit) if row["run_id"] not in seen]
        return (live + stored)[:limit]

    async def wait(self, project_id: str, run_id: str, timeout: float | None = None) -> RunHandle | None:
        """Wait until the run has finished. Meant for tests and for callers that want to block."""
        handle = self._own(project_id, run_id)
        task = self._tasks.get(run_id)
        if handle is None:
            return None
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        return handle

    # -- control --------------------------------------------------------------------------
    async def cancel(self, project_id: str, run_id: str) -> bool:
        """Ask a run to stop. Cases already running finish; the rest are skipped. ``False`` when there is
        nothing to cancel (unknown, another project's, or already finished)."""
        handle = self._own(project_id, run_id)
        event = self._cancel_events.get(run_id)
        if handle is None or handle.state != STATE_RUNNING or event is None:
            return False
        event.set()
        self._emit({"event": "evaluation.run.cancel_requested", "run_id": run_id, "project_id": project_id})
        return True

    async def shutdown(self, *, grace_seconds: float = 10.0) -> None:
        """Stop accepting runs, ask the running ones to stop and wait for them."""
        self._closed = True
        for event in list(self._cancel_events.values()):
            event.set()
        tasks = [t for t in self._tasks.values() if not t.done()]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=grace_seconds)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
