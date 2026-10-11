"""Worker API tests using a fake engine. They verify the HTTP contract, not the model."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import WorkerSettings
from app.engine import ContextLengthExceeded, EngineState, GenerationOutput
from app.main import Admission, WorkerError, create_app

TOKEN = "w" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BODY = {"messages": [{"role": "user", "content": "Hello"}], "max_new_tokens": 32}


class FakeEngine:
    """Test double standing in for the model. Only used in tests, never at runtime."""

    def __init__(self, state=EngineState.READY, error: Exception | None = None):
        self._state = state
        self._error = error
        self.calls: list[dict] = []

    @property
    def state(self):
        return self._state

    def info(self):
        return {"state": self._state.value, "model_id": "NCAIR1/N-ATLaS"}

    def generate(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self._error is not None:
            raise self._error
        return GenerationOutput("Hello from the test double", "stop", 11, 5, 42)


def make_client(engine, **overrides) -> TestClient:
    settings = WorkerSettings(_env_file=None, token=TOKEN, load_on_startup=False, **overrides)
    return TestClient(create_app(settings, engine=engine))


def test_healthz_is_open_and_reveals_nothing():
    response = make_client(FakeEngine()).get("/healthz")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


def test_info_and_chat_require_the_shared_token():
    client = make_client(FakeEngine())
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Basic {TOKEN}"}):
        assert client.get("/v1/info", headers=headers).status_code == 401
        denied = client.post("/v1/chat", json=BODY, headers=headers)
        assert denied.status_code == 401
        assert denied.json()["error"]["code"] == "unauthorized"
    assert client.get("/v1/info", headers=AUTH).json()["state"] == "ready"


def test_successful_chat_returns_the_contract_fields_and_passes_parameters():
    engine = FakeEngine()
    response = make_client(engine).post(
        "/v1/chat",
        json={**BODY, "temperature": 0.3, "top_p": 0.9, "repetition_penalty": 1.1},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "text": "Hello from the test double",
        "finish_reason": "stop",
        "prompt_tokens": 11,
        "completion_tokens": 5,
        "model_id": "NCAIR1/N-ATLaS",
        "generation_ms": 42,
    }
    call = engine.calls[0]
    assert call["max_new_tokens"] == 32 and call["temperature"] == 0.3
    assert call["top_p"] == 0.9 and call["repetition_penalty"] == 1.1
    assert call["messages"] == [{"role": "user", "content": "Hello"}]


def test_invalid_requests_are_rejected_with_the_invalid_request_code():
    client = make_client(FakeEngine())
    bad_bodies = [
        {"messages": []},
        {"messages": [{"role": "robot", "content": "x"}]},
        {**BODY, "unexpected": True},
        {**BODY, "temperature": 9},
    ]
    for body in bad_bodies:
        response = client.post("/v1/chat", json=body, headers=AUTH)
        assert response.status_code == 422, body
        assert response.json()["error"]["code"] == "invalid_request"


def test_limits_from_settings_are_enforced():
    client = make_client(FakeEngine(), max_new_tokens_limit=16, max_prompt_chars=100)
    too_many_tokens = client.post("/v1/chat", json={**BODY, "max_new_tokens": 17}, headers=AUTH)
    assert too_many_tokens.status_code == 422
    long_prompt = client.post(
        "/v1/chat",
        json={"messages": [{"role": "user", "content": "x" * 101}], "max_new_tokens": 8},
        headers=AUTH,
    )
    assert long_prompt.status_code == 422


def test_loading_and_failed_states_never_produce_output():
    loading = make_client(FakeEngine(EngineState.LOADING)).post("/v1/chat", json=BODY, headers=AUTH)
    assert loading.status_code == 503
    assert loading.json()["error"]["code"] == "model_loading"
    assert loading.headers["retry-after"] == "15"
    failed = make_client(FakeEngine(EngineState.FAILED)).post("/v1/chat", json=BODY, headers=AUTH)
    assert failed.status_code == 503 and failed.json()["error"]["code"] == "model_failed"


def test_context_length_exceeded_is_reported_with_numbers():
    engine = FakeEngine(error=ContextLengthExceeded(9000, 512, 8092))
    response = make_client(engine).post("/v1/chat", json=BODY, headers=AUTH)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "context_length_exceeded"
    assert "9000" in error["message"] and "8092" in error["message"]


def test_unexpected_generation_failures_do_not_leak_details():
    engine = FakeEngine(error=RuntimeError("secret internal path /srv/models/x"))
    response = make_client(engine).post("/v1/chat", json=BODY, headers=AUTH)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "generation_failed"
    assert "/srv/models" not in response.text


def test_out_of_memory_is_reported_as_busy():
    class OutOfMemoryError(RuntimeError):
        pass

    response = make_client(FakeEngine(error=OutOfMemoryError("CUDA out of memory"))).post(
        "/v1/chat", json=BODY, headers=AUTH
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "busy"


def test_admission_rejects_when_the_queue_is_full():
    async def scenario():
        admission = Admission(max_concurrency=1, max_queue=0)
        async with admission.slot():
            with pytest.raises(WorkerError) as rejected:
                async with admission.slot():
                    pass
        assert rejected.value.status_code == 429 and rejected.value.code == "busy"
        async with admission.slot():  # a free slot is usable again
            pass

    asyncio.run(scenario())


def test_weak_worker_tokens_are_rejected():
    for token in ("short", "CHANGE_ME_" + "x" * 40):
        with pytest.raises(ValueError):
            WorkerSettings(_env_file=None, token=token)
