"""Evaluation module: the adapter that lets the engine call the platform gateway, and the
translation of errors in both directions. No database and no network."""

import asyncio
import uuid

from forge_integrations.errors import ErrorCode, ForgeError, ValidationFailed, ValidationIssue
from forge_integrations.ports import ChatMessage as LibraryMessage
from forge_integrations.ports import GenerationRequest

from app.core.errors import AppError, RateLimited, UnprocessableEntity
from app.core.scopes import SCOPE_CHAT_CREATE
from app.integrations.contracts import GatewayChatOutput
from app.modules.evaluation.errors import to_app_error, to_forge_error
from app.modules.evaluation.gateway_adapter import (
    EVALUATION_ENDPOINT,
    MAX_WAIT_SECONDS,
    RATE_LIMIT_RETRIES,
    PlatformModelGateway,
)
from app.providers.base import Usage

PROJECT_ID = uuid.UUID("5b1f0b0e-7d6e-4c58-9d0e-2f3c4a5b6c7d")


def make_request(**overrides):
    values = {
        "messages": (
            LibraryMessage("system", "Be brief."),
            LibraryMessage("user", "Say hello."),
        ),
        "model": "NCAIR1/N-ATLaS",
        "temperature": 0.2,
        "max_tokens": 64,
        "language": "yo",
        "request_id": "a" * 32,
        "project_id": str(PROJECT_ID),
        "source": "evaluation",
    }
    return GenerationRequest(**{**values, **overrides})


def make_output():
    return GatewayChatOutput(
        request_id="a" * 32,
        model="NCAIR1/N-ATLaS",
        text="Hello!",
        finish_reason="stop",
        usage=Usage(prompt_tokens=11, completion_tokens=3),
        latency_ms=321,
    )


class FakeGateway:
    """Stands in for GatewayService. Items in `script` are returned, or raised if exceptions."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    async def chat(self, principal, payload, *, request_id, endpoint, method="POST"):
        self.calls.append((principal, payload, request_id, endpoint))
        item = self.script.pop(0) if self.script else make_output()
        if isinstance(item, Exception):
            raise item
        return item


def make_adapter(gateway):
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    return PlatformModelGateway(lambda: gateway, sleep=fake_sleep), waits


def test_a_call_is_translated_and_goes_through_the_gateway_as_an_evaluation_request():
    gateway = FakeGateway()
    adapter, waits = make_adapter(gateway)

    result = asyncio.run(adapter.generate(make_request()))

    principal, payload, request_id, endpoint = gateway.calls[0]
    assert principal.source == "evaluation" and principal.project_id == PROJECT_ID
    assert principal.scopes == frozenset({SCOPE_CHAT_CREATE})
    assert principal.api_key_id is None and principal.user_id is None
    assert [(m.role, m.content) for m in payload.messages] == [
        ("system", "Be brief."),
        ("user", "Say hello."),
    ]
    assert (payload.model, payload.max_tokens, payload.temperature) == ("NCAIR1/N-ATLaS", 64, 0.2)
    assert payload.language == "yo" and payload.top_p is None
    assert request_id == "a" * 32 and endpoint == EVALUATION_ENDPOINT
    assert "{project_id}" in EVALUATION_ENDPOINT  # a template, so logs stay low-cardinality
    assert result.text == "Hello!" and result.model == "NCAIR1/N-ATLaS"
    assert (result.usage.prompt_tokens, result.usage.completion_tokens) == (11, 3)
    assert result.usage.total_tokens == 14
    assert result.latency_ms == 321.0 and result.finish_reason == "stop"
    assert waits == []


def test_a_request_without_an_id_gets_one():
    gateway = FakeGateway()
    adapter, _ = make_adapter(gateway)
    asyncio.run(adapter.generate(make_request(request_id=None)))
    assert len(gateway.calls[0][2]) == 32


def test_a_throttled_call_waits_and_tries_again():
    gateway = FakeGateway(
        RateLimited(headers={"Retry-After": "2"}), RateLimited(headers={"Retry-After": "7"})
    )
    adapter, waits = make_adapter(gateway)

    result = asyncio.run(adapter.generate(make_request()))

    assert result.text == "Hello!"
    assert waits == [2.0, 7.0] and len(gateway.calls) == 3


def test_waits_are_bounded_and_the_call_gives_up_with_the_rate_limit_code():
    gateway = FakeGateway(*[RateLimited(headers={"Retry-After": "999"}) for _ in range(10)])
    adapter, waits = make_adapter(gateway)

    try:
        asyncio.run(adapter.generate(make_request()))
    except ForgeError as error:
        assert error.code == "rate_limited" and error.http_status == 429 and error.retryable
    else:
        raise AssertionError("the call should have failed")

    assert waits == [MAX_WAIT_SECONDS] * RATE_LIMIT_RETRIES
    assert len(gateway.calls) == RATE_LIMIT_RETRIES + 1


def test_a_missing_or_unreadable_retry_hint_still_waits_a_sensible_time():
    gateway = FakeGateway(RateLimited(), RateLimited(headers={"Retry-After": "soon"}))
    adapter, waits = make_adapter(gateway)
    asyncio.run(adapter.generate(make_request()))
    assert waits == [1.0, 1.0]


def test_other_errors_are_not_retried_and_keep_their_code():
    cases = [
        (AppError("The model host is away.", code="provider_unavailable", status_code=503), True),
        (AppError("Too slow.", code="provider_timeout", status_code=504), True),
        (AppError("No such model.", code="model_not_found", status_code=404), False),
        (AppError("Bad input.", code="invalid_messages", status_code=422), False),
    ]
    for exc, retryable in cases:
        gateway = FakeGateway(exc)
        adapter, waits = make_adapter(gateway)
        try:
            asyncio.run(adapter.generate(make_request()))
        except ForgeError as error:
            assert (error.code, error.http_status) == (exc.code, exc.status_code)
            assert error.message == exc.message and error.retryable is retryable
        else:
            raise AssertionError("the call should have failed")
        assert waits == [] and len(gateway.calls) == 1


def test_a_call_without_a_usable_project_is_an_internal_error_and_never_reaches_the_gateway():
    for project_id in (None, "", "not-a-uuid"):
        gateway = FakeGateway()
        adapter, _ = make_adapter(gateway)
        try:
            asyncio.run(adapter.generate(make_request(project_id=project_id)))
        except ForgeError as error:
            assert error.code == ErrorCode.INTERNAL and error.http_status == 500
        else:
            raise AssertionError("the call should have failed")
        assert gateway.calls == []


def test_the_gateway_is_looked_up_on_every_call():
    first, second = FakeGateway(), FakeGateway()
    current = {"gateway": first}
    adapter = PlatformModelGateway(lambda: current["gateway"])
    asyncio.run(adapter.generate(make_request()))
    current["gateway"] = second  # the integration tests replace app.state.gateway like this
    asyncio.run(adapter.generate(make_request()))
    assert len(first.calls) == 1 and len(second.calls) == 1


def test_platform_errors_become_library_errors():
    error = to_forge_error(AppError("Down.", code="provider_unavailable", status_code=503))
    assert (error.code, error.message, error.http_status, error.retryable) == (
        "provider_unavailable",
        "Down.",
        503,
        True,
    )


def test_library_errors_become_platform_errors_in_the_standard_shape():
    forge = ForgeError(ErrorCode.NOT_FOUND, "No such starter dataset.", http_status=404)
    app_error = to_app_error(forge)
    assert isinstance(app_error, AppError)
    assert (app_error.code, app_error.status_code) == (ErrorCode.NOT_FOUND, 404)
    assert app_error.message == "No such starter dataset."


def test_dataset_problems_become_a_422_with_a_field_and_a_message_for_each():
    failure = ValidationFailed(
        [ValidationIssue("cases[0].prompt", "Required."), ValidationIssue("name", "Too long.")]
    )
    app_error = to_app_error(failure)
    assert isinstance(app_error, UnprocessableEntity)
    assert (app_error.status_code, app_error.code) == (422, "invalid_dataset")
    assert app_error.details == [
        {"field": "cases[0].prompt", "message": "Required."},
        {"field": "name", "message": "Too long."},
    ]


def test_a_very_long_problem_list_is_cut_short():
    failure = ValidationFailed([ValidationIssue(f"cases[{i}]", "Bad.") for i in range(500)])
    assert len(to_app_error(failure).details) == 50
