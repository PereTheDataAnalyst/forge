"""MCP adapters that need no database: model gateway, rate limiter and speech provider."""

import asyncio
import dataclasses
import uuid
from types import SimpleNamespace

from forge_integrations.errors import ErrorCode, ForgeError
from forge_integrations.ports import ChatMessage, GenerationRequest

from app.core.errors import AppError, RateLimited, ServiceUnavailable
from app.integrations.contracts import GatewayChatOutput
from app.modules.mcp.adapters import (
    MCP_ENDPOINT,
    McpLogObserver,
    McpModelGateway,
    PlatformRateLimiter,
    SharedAsrProvider,
    to_forge_error,
)
from app.providers.base import Usage


class FakeGateway:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def chat(self, principal, payload, *, request_id, endpoint):
        self.calls.append((principal, payload, request_id, endpoint))
        if self.error:
            raise self.error
        return GatewayChatOutput(
            request_id=request_id,
            model="NCAIR1/N-ATLaS",
            text="Hello.",
            finish_reason="stop",
            usage=Usage(prompt_tokens=5, completion_tokens=2),
            latency_ms=321,
        )


def _request(project_id=None, **overrides):
    base = dict(
        messages=(ChatMessage(role="user", content="Say hello"),),
        model=None,
        max_tokens=64,
        temperature=0.2,
        language="yo",
        project_id=str(project_id or uuid.uuid4()),
        request_id="req_mcp_000000000001",
    )
    base.update(overrides)
    return GenerationRequest(**base)


# --- gateway ----------------------------------------------------------------------------
def test_a_tool_call_is_a_normal_gateway_request_with_source_mcp():
    gateway = FakeGateway()
    adapter = McpModelGateway(lambda: gateway)
    project_id = uuid.uuid4()
    result = asyncio.run(adapter.generate(_request(project_id)))
    assert result.text == "Hello." and result.model == "NCAIR1/N-ATLaS"
    assert result.usage.total_tokens == 7 and result.latency_ms == 321.0
    ((principal, payload, request_id, endpoint),) = gateway.calls
    assert principal.source == "mcp" and principal.project_id == project_id
    assert principal.api_key_id is None and principal.user_id is None
    assert principal.scopes == frozenset({"chat.create"})  # only what chat needs
    assert request_id == "req_mcp_000000000001" and endpoint == MCP_ENDPOINT == "/mcp"
    assert payload.max_tokens == 64 and payload.language == "yo"
    assert payload.messages[0].role == "user" and payload.messages[0].content == "Say hello"


def test_the_gateway_is_looked_up_on_each_call_so_it_can_be_replaced():
    first, second = FakeGateway(), FakeGateway()
    holder = {"gateway": first}
    adapter = McpModelGateway(lambda: holder["gateway"])
    asyncio.run(adapter.generate(_request()))
    holder["gateway"] = second
    asyncio.run(adapter.generate(_request()))
    assert len(first.calls) == 1 and len(second.calls) == 1


def test_a_throttled_call_is_reported_at_once_and_never_waits_or_retries():
    error = RateLimited("slow down", code="rate_limited", headers={"Retry-After": "7"})
    gateway = FakeGateway(error)
    try:
        asyncio.run(McpModelGateway(lambda: gateway).generate(_request()))
    except ForgeError as exc:
        assert exc.code == "rate_limited" and exc.http_status == 429 and exc.retryable
        assert exc.headers and exc.headers.get("Retry-After") == "7"
    else:
        raise AssertionError("must raise")
    assert len(gateway.calls) == 1


def test_provider_errors_keep_their_code_and_status():
    error = ServiceUnavailable("model offline", code="provider_unavailable")
    try:
        asyncio.run(McpModelGateway(lambda: FakeGateway(error)).generate(_request()))
    except ForgeError as exc:
        assert exc.code == "provider_unavailable" and exc.http_status == 503 and exc.retryable
    else:
        raise AssertionError("must raise")


def test_a_client_error_is_not_marked_retryable():
    exc = to_forge_error(AppError("bad", code="invalid_request", status_code=422))
    assert exc.http_status == 422 and not exc.retryable and not exc.headers


def test_a_call_without_a_valid_project_is_refused_before_the_gateway():
    for project_id in (None, "", "not-a-uuid"):
        gateway = FakeGateway()
        request = dataclasses.replace(_request(), project_id=project_id)
        try:
            asyncio.run(McpModelGateway(lambda: gateway).generate(request))
        except ForgeError as exc:
            assert exc.code == ErrorCode.INTERNAL
        else:
            raise AssertionError("must raise")
        assert gateway.calls == []


# --- rate limiter -----------------------------------------------------------------------
class FakeLimiter:
    def __init__(self, allowed=True):
        self.allowed, self.calls = allowed, []

    async def hit(self, key, limit, window_seconds):
        self.calls.append((key, limit, window_seconds))
        return SimpleNamespace(allowed=self.allowed, remaining=4, reset_seconds=9)


def test_the_library_limiter_is_the_platform_limiter_with_a_namespaced_key():
    limiter = FakeLimiter()
    decision = asyncio.run(PlatformRateLimiter(lambda: limiter).check("gen:abc", 20, 60))
    assert decision.allowed and decision.remaining == 4 and decision.retry_after_seconds == 9
    assert limiter.calls == [("rl:mcp:gen:abc", 20, 60)]


def test_a_refusal_is_passed_on_and_a_long_key_is_bounded():
    limiter = FakeLimiter(allowed=False)
    decision = asyncio.run(PlatformRateLimiter(lambda: limiter).check("k" * 500, 1, 60))
    assert not decision.allowed
    assert len(limiter.calls[0][0]) <= 190


def test_if_the_limiter_fails_the_error_reaches_the_library_which_then_refuses():
    class Broken:
        async def hit(self, *args):
            raise ServiceUnavailable("redis down", code="rate_limiter_unavailable")

    try:
        asyncio.run(PlatformRateLimiter(lambda: Broken()).check("x", 1, 60))
    except ServiceUnavailable:
        return
    raise AssertionError("must not swallow the failure and allow the call")


# --- speech -----------------------------------------------------------------------------
class FakeVoiceRuntime:
    def __init__(self, provider=None, error=None, slots=1):
        self._provider, self.error, self.slots = provider, error, slots
        self.released = 0

    async def get_provider(self):
        return self._provider

    def try_acquire(self):
        if self.slots <= 0:
            return False
        self.slots -= 1
        return True

    def release(self):
        self.released += 1


class FakeAsr:
    def __init__(self, error=None):
        self.error, self.calls = error, []

    async def transcribe(self, *, audio, audio_format, language, request_id=None):
        self.calls.append((len(audio), audio_format, language, request_id))
        if self.error:
            raise self.error
        return "result"


def _transcribe(runtime):
    return asyncio.run(
        SharedAsrProvider(runtime).transcribe(
            audio=b"x" * 10, audio_format="wav", language="yo", request_id="r1"
        )
    )


def test_speech_uses_the_shared_worker_client_and_gives_the_slot_back():
    asr = FakeAsr()
    runtime = FakeVoiceRuntime(provider=asr)
    assert _transcribe(runtime) == "result"
    assert asr.calls == [(10, "wav", "yo", "r1")] and runtime.released == 1


def test_the_slot_is_given_back_when_the_worker_fails():
    runtime = FakeVoiceRuntime(provider=FakeAsr(error=ForgeError("provider_error", "bad", http_status=502)))
    try:
        _transcribe(runtime)
    except ForgeError:
        pass
    else:
        raise AssertionError("must raise")
    assert runtime.released == 1


def test_no_worker_gives_provider_not_configured_and_an_unusable_one_gives_unavailable():
    for runtime, code in (
        (FakeVoiceRuntime(provider=None, error=None), "provider_not_configured"),
        (FakeVoiceRuntime(provider=None, error="outbound_blocked"), "provider_unavailable"),
    ):
        try:
            _transcribe(runtime)
        except ForgeError as exc:
            assert exc.code == code and exc.http_status == 503
        else:
            raise AssertionError("must raise")
        assert runtime.released == 0


def test_a_busy_worker_is_reported_as_retryable_without_taking_a_slot():
    runtime = FakeVoiceRuntime(provider=FakeAsr(), slots=0)
    try:
        _transcribe(runtime)
    except ForgeError as exc:
        assert exc.code == "provider_unavailable" and exc.retryable
        assert exc.headers and exc.headers.get("Retry-After") == "2"
    else:
        raise AssertionError("must raise")
    assert runtime.released == 0


# --- observer ---------------------------------------------------------------------------
def test_the_observer_never_collides_with_reserved_logging_attributes():
    observer = McpLogObserver()
    observer.emit(
        {
            "event": "mcp.tool_call",
            "name": "generate",
            "message": "x",
            "module": "y",
            "args": 1,
            "request_id": "req_1",
            "duration_ms": 12.5,
        }
    )
