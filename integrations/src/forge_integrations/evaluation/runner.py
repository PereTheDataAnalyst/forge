"""Evaluation runner.

Executes a validated dataset through a ``ModelGateway`` with bounded concurrency,
per-call timeouts and cooperative cancellation, then applies the deterministic
checks. Provider failures become ``error`` results, never exceptions, and never
carry secrets or stack traces.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from ..errors import ErrorCode, ForgeError
from ..ports import ChatMessage, GenerationRequest, ModelGateway, Observer
from ..security.redaction import redact_text
from .evaluators import CheckContext, evaluate_check
from .metrics import METHODOLOGY, summarize
from .models import (
    MAX_STORED_OUTPUT_CHARS,
    STATUS_ERROR,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_REVIEW,
    STATUS_SKIPPED,
    Attempt,
    Case,
    CaseResult,
    Dataset,
    Run,
    RunConfig,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class EvaluationRunner:
    def __init__(
        self,
        gateway: ModelGateway,
        *,
        observer: Observer | None = None,
        now: Callable[[], str] = _utc_now,
        perf: Callable[[], float] = time.perf_counter,
        new_id: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._gateway = gateway
        self._observer = observer
        self._now = now
        self._perf = perf
        self._new_id = new_id

    def _emit(self, event: dict) -> None:
        if self._observer is not None:
            try:
                self._observer.emit(event)
            except Exception:  # noqa: BLE001 - observability must never break a run
                pass

    async def run(
        self,
        dataset: Dataset,
        config: RunConfig | None = None,
        *,
        project_id: str | None = None,
        run_id: str | None = None,
        cancel: asyncio.Event | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> Run:
        """Run every case. ``progress(done, total)`` is called after each case finishes; a failing
        callback is ignored so a broken progress display can never fail a run."""
        cfg = config or RunConfig()
        if not 1 <= cfg.max_concurrency <= 16:
            raise ValueError("max_concurrency must be between 1 and 16")
        rid = run_id or self._new_id()
        started = self._now()
        semaphore = asyncio.Semaphore(cfg.max_concurrency)
        results: list[CaseResult | None] = [None] * len(dataset.cases)
        total = len(dataset.cases)
        finished_cases = 0

        async def worker(index: int, case: Case) -> None:
            nonlocal finished_cases
            async with semaphore:
                results[index] = await self._run_case(case, cfg, project_id, rid, cancel)
            finished_cases += 1
            if progress is not None:
                try:
                    progress(finished_cases, total)
                except Exception:  # noqa: BLE001
                    pass

        await asyncio.gather(*(worker(i, c) for i, c in enumerate(dataset.cases)))
        final = [r for r in results if r is not None]
        status = "cancelled" if any(r.status == STATUS_SKIPPED for r in final) else "completed"
        summary = summarize(final)
        run = Run(
            run_id=rid,
            dataset_id=dataset.id,
            dataset_name=dataset.name,
            dataset_version=dataset.version,
            model=cfg.model,
            prompt_version=cfg.prompt_version,
            project_id=project_id,
            status=status,
            started_at=started,
            finished_at=self._now(),
            config=cfg.to_dict(),
            results=final,
            summary=summary,
            methodology=METHODOLOGY,
        )
        self._emit(
            {
                "event": "evaluation.run.finished",
                "run_id": rid,
                "project_id": project_id,
                "status": status,
                "total": summary["total"],
                "counts": summary["counts"],
            }
        )
        return run

    async def _run_case(
        self,
        case: Case,
        cfg: RunConfig,
        project_id: str | None,
        run_id: str,
        cancel: asyncio.Event | None,
    ) -> CaseResult:
        attempts: list[Attempt] = []
        for index in range(case.repeat):
            if cancel is not None and cancel.is_set():
                break
            attempts.append(await self._attempt(case, cfg, project_id, run_id, index))

        base = dict(case_id=case.id, name=case.name, category=case.category, language=case.language)
        if not attempts:
            return CaseResult(status=STATUS_SKIPPED, **base)

        good = [a for a in attempts if a.ok]
        failed_count = len(attempts) - len(good)
        if not good:
            first = attempts[0]
            return CaseResult(
                status=STATUS_ERROR,
                attempts=attempts,
                error_code=first.error_code,
                error_message=first.error_message,
                partial_error_count=failed_count,
                **base,
            )

        ctx = CheckContext(
            case=case,
            outputs=tuple(a.output or "" for a in good),
            latencies_ms=tuple(a.latency_ms for a in good),
        )
        check_results = [evaluate_check(check, ctx) for check in case.checks]
        if any(c.passed is False for c in check_results):
            status = STATUS_FAIL
        elif any(c.passed is None for c in check_results):
            status = STATUS_REVIEW
        else:
            status = STATUS_PASS
        return CaseResult(
            status=status,
            attempts=attempts,
            checks=check_results,
            partial_error_count=failed_count,
            **base,
        )

    async def _attempt(
        self, case: Case, cfg: RunConfig, project_id: str | None, run_id: str, index: int
    ) -> Attempt:
        request_id = self._new_id()
        messages: list[ChatMessage] = []
        if case.system:
            messages.append(ChatMessage("system", case.system))
        messages.append(ChatMessage("user", case.prompt))
        request = GenerationRequest(
            messages=tuple(messages),
            model=cfg.model,
            temperature=case.temperature if case.temperature is not None else cfg.default_temperature,
            max_tokens=case.max_tokens if case.max_tokens is not None else cfg.default_max_tokens,
            language=case.language,
            request_id=request_id,
            project_id=project_id,
            source="evaluation",
        )
        started = self._perf()
        attempt: Attempt
        try:
            result = await asyncio.wait_for(self._gateway.generate(request), timeout=cfg.call_timeout_seconds)
            elapsed = (self._perf() - started) * 1000
            text = result.text or ""
            truncated = len(text) > MAX_STORED_OUTPUT_CHARS
            attempt = Attempt(
                index=index,
                request_id=request_id,
                ok=True,
                latency_ms=elapsed,
                output=text[:MAX_STORED_OUTPUT_CHARS],
                output_truncated=truncated,
                provider_latency_ms=result.latency_ms,
                model=result.model,
                usage=None
                if result.usage is None
                else {
                    "prompt_tokens": result.usage.prompt_tokens,
                    "completion_tokens": result.usage.completion_tokens,
                    "total_tokens": result.usage.total_tokens,
                },
                finish_reason=result.finish_reason,
            )
        except asyncio.TimeoutError:
            attempt = Attempt(
                index=index,
                request_id=request_id,
                ok=False,
                latency_ms=(self._perf() - started) * 1000,
                error_code=ErrorCode.PROVIDER_TIMEOUT,
                error_message=f"The model call did not finish within {cfg.call_timeout_seconds:g} seconds.",
            )
        except ForgeError as exc:
            attempt = Attempt(
                index=index,
                request_id=request_id,
                ok=False,
                latency_ms=(self._perf() - started) * 1000,
                error_code=exc.code,
                error_message=redact_text(exc.message),
            )
        except Exception:  # noqa: BLE001 - never leak provider internals into results
            attempt = Attempt(
                index=index,
                request_id=request_id,
                ok=False,
                latency_ms=(self._perf() - started) * 1000,
                error_code=ErrorCode.INTERNAL,
                error_message="The gateway raised an unexpected error.",
            )
        self._emit(
            {
                "event": "evaluation.attempt",
                "run_id": run_id,
                "project_id": project_id,
                "case_id": case.id,
                "attempt": index,
                "request_id": request_id,
                "ok": attempt.ok,
                "latency_ms": round(attempt.latency_ms, 2),
                "error_code": attempt.error_code,
                "language": case.language,
            }
        )
        return attempt
