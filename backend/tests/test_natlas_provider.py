"""NAtlasProvider behaviour against simulated worker responses (no network, no services)."""

import asyncio

import httpx
import pytest

from app.providers.base import (
    ChatMessage,
    ChatRequest,
    GenerationParams,
    ProviderBusy,
    ProviderError,
    ProviderLoading,
    ProviderMisconfigured,
    ProviderNotConfigured,
    ProviderProtocolError,
    ProviderRejected,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.providers.natlas import NAtlasProvider
from tests.conftest import make_settings

WORKER = "http://worker.invalid:8081"
REQUEST = ChatRequest(
    model="NCAIR1/N-ATLaS",
    messages=(ChatMessage(role="user", content="Hello"),),
    params=GenerationParams(max_tokens=64, temperature=0.1, top_p=1.0, repetition_penalty=1.12),
    request_id="req_test_000001",
)
GOOD_BODY = {
    "text": "Hi there",
    "finish_reason": "stop",
    "prompt_tokens": 12,
    "completion_tokens": 3,
    "model_id": "NCAIR1/N-ATLaS",
}


def provider_with(handler) -> NAtlasProvider:
    settings = make_settings(natlas_inference_url=WORKER, natlas_inference_token="w" * 40)
    provider = NAtlasProvider(settings)
    provider._client = httpx.AsyncClient(
        base_url=WORKER, transport=httpx.MockTransport(handler), follow_redirects=False
    )
    return provider


def chat(provider: NAtlasProvider):
    return asyncio.run(provider.chat(REQUEST))


def test_successful_chat_sends_expected_payload_and_parses_result():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["request_id"] = request.headers.get("x-request-id")
        seen["body"] = request.content
        return httpx.Response(200, json=GOOD_BODY)

    result = chat(provider_with(handler))
    assert seen["path"] == "/v1/chat"
    assert seen["request_id"] == "req_test_000001"
    assert b'"max_new_tokens":64' in seen["body"].replace(b" ", b"")
    assert b'"repetition_penalty":1.12' in seen["body"].replace(b" ", b"")
    assert result.text == "Hi there" and result.finish_reason == "stop"
    assert result.usage.total_tokens == 15
    assert result.model == "NCAIR1/N-ATLaS"


def test_error_responses_map_to_specific_provider_errors():
    def respond(status, body, headers=None):
        return lambda request: httpx.Response(status, json=body, headers=headers or {})

    with pytest.raises(ProviderLoading) as loading:
        chat(provider_with(respond(503, {"error": {"code": "model_loading"}}, {"Retry-After": "7"})))
    assert loading.value.retry_after == 7

    with pytest.raises(ProviderBusy):
        chat(provider_with(respond(429, {"error": {"code": "busy"}})))

    with pytest.raises(ProviderRejected) as rejected:
        chat(
            provider_with(
                respond(
                    422,
                    {"error": {"code": "context_length_exceeded", "message": "Prompt has 9000 tokens."}},
                )
            )
        )
    assert rejected.value.code == "context_length_exceeded"
    assert "9000" in rejected.value.message

    with pytest.raises(ProviderMisconfigured):
        chat(provider_with(respond(401, {"error": {"code": "unauthorized"}})))

    with pytest.raises(ProviderUnavailable):
        chat(provider_with(respond(503, {"error": {"code": "model_failed"}})))

    with pytest.raises(ProviderError) as generic:
        chat(provider_with(respond(500, {"error": {"code": "boom", "message": "secret path /srv/x"}})))
    assert generic.value.code == "provider_error"
    assert "/srv/x" not in generic.value.message


def test_unknown_rejection_codes_are_not_passed_through():
    def handler(request):
        return httpx.Response(422, json={"error": {"code": "attacker_defined", "message": "x"}})

    with pytest.raises(ProviderError) as error:
        chat(provider_with(handler))
    assert error.value.code == "provider_error"


def test_malformed_success_responses_are_protocol_errors():
    bad_bodies = [
        {"finish_reason": "stop", "prompt_tokens": 1, "completion_tokens": 1},
        {**GOOD_BODY, "finish_reason": "banana"},
        {**GOOD_BODY, "prompt_tokens": True},
        {**GOOD_BODY, "completion_tokens": -1},
        {**GOOD_BODY, "text": 123},
        ["not", "an", "object"],
    ]
    for body in bad_bodies:
        with pytest.raises(ProviderProtocolError):
            chat(provider_with(lambda request, b=body: httpx.Response(200, json=b)))
    with pytest.raises(ProviderProtocolError):
        chat(provider_with(lambda request: httpx.Response(200, content=b"<html>not json</html>")))


def test_network_failures_map_to_timeout_and_unavailable():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ProviderTimeout):
        chat(provider_with(timeout))
    with pytest.raises(ProviderUnavailable):
        chat(provider_with(refused))


def test_unconfigured_provider_never_pretends_to_work():
    provider = NAtlasProvider(make_settings())
    with pytest.raises(ProviderNotConfigured):
        asyncio.run(provider.chat(REQUEST))
    health = asyncio.run(provider.health())
    assert health.status == "not_configured"
    models = asyncio.run(provider.list_models())
    assert models[0].status == "not_configured"


def test_health_states():
    def info(status, body):
        return lambda request: httpx.Response(status, json=body)

    assert asyncio.run(provider_with(info(200, {"state": "ready", "model_id": "m"})).health()).status == "ok"
    assert asyncio.run(provider_with(info(200, {"state": "loading"})).health()).status == "loading"
    assert asyncio.run(provider_with(info(200, {"state": "failed"})).health()).status == "error"
    assert asyncio.run(provider_with(info(401, {})).health()).status == "error"
    assert asyncio.run(provider_with(info(200, {"state": "???"})).health()).status == "error"

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    assert asyncio.run(provider_with(refused).health()).status == "unreachable"


def test_health_probe_results_are_cached():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"state": "ready", "model_id": "NCAIR1/N-ATLaS"})

    provider = provider_with(handler)

    async def probe_twice():
        await provider.health()
        await provider.health()

    asyncio.run(probe_twice())
    assert calls == ["/v1/info"]


def test_model_listing_reflects_worker_state():
    provider = provider_with(lambda request: httpx.Response(200, json={"state": "ready"}))
    model = asyncio.run(provider.list_models())[0]
    assert model.id == "NCAIR1/N-ATLaS" and model.status == "available"
    assert model.languages == ("en", "ha", "ig", "yo")
    assert provider.supports_model("NCAIR1/N-ATLaS")
    assert not provider.supports_model("gpt-4o")
