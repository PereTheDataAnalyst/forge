"""Voice orchestration: validation, limits, the speech worker, and what is recorded.

No database, Redis, web framework or speech worker is needed. A real WAV file is generated, a fake
worker answers, and a spy records what would be stored.
"""

import asyncio
import dataclasses
import uuid
from types import SimpleNamespace

from forge_integrations.errors import ForgeError
from forge_integrations.natlas_facts import ASR_MODELS

from app.core.errors import AppError, RateLimited
from app.modules.voice.runtime import VoiceRuntime
from tests.voice_double import TRANSCRIPT, FakeAsr, make_wav

from app.modules.voice.service import (
    API_ENDPOINT,
    BROWSER_ENDPOINT,
    VoiceCaller,
    VoiceRecord,
    VoiceService,
)

PROJECT_ID = uuid.uuid4()


def _settings(**overrides):
    base = dict(
        asr_configured=False,
        voice_rate_limit_per_minute=10,
        voice_max_upload_bytes=1_000_000,
        voice_max_seconds=30,
        voice_max_concurrent=2,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeLimiter:
    def __init__(self, error=None):
        self.error, self.keys = error, []

    async def enforce(self, key, limit, window_seconds, *, code, message):
        self.keys.append((key, limit, window_seconds))
        if self.error:
            raise self.error
        return SimpleNamespace(
            headers=lambda: {"X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": "9"}
        )


class Spy:
    def __init__(self, fail=False):
        self.records: list[VoiceRecord] = []
        self.fail = fail

    async def __call__(self, record):
        if self.fail:
            raise RuntimeError("database down")
        self.records.append(record)


def build(settings=None, provider=None, factory=None, limiter=None, spy=None):
    settings = settings or _settings()
    runtime = VoiceRuntime(settings, factory=factory or (lambda s: None))
    if provider is not None:
        runtime.set_provider(provider)
    limiter = limiter or FakeLimiter()
    spy = spy or Spy()
    service = VoiceService(settings=settings, runtime=runtime, limiter=limiter, recorder=spy)
    return service, runtime, limiter, spy


def run(service, audio, *, mime="audio/wav", language="yo", caller=None):
    caller = caller or VoiceCaller(project_id=PROJECT_ID, api_key_id=uuid.uuid4())
    return asyncio.run(
        service.transcribe(
            caller, audio=audio, declared_mime=mime, language=language, request_id="req_voice_00001"
        )
    )


def fails(service, audio, **kwargs) -> AppError:
    try:
        run(service, audio, **kwargs)
    except AppError as exc:
        return exc
    raise AssertionError("expected an AppError")


# --- success --------------------------------------------------------------------------------
def test_a_clip_is_transcribed_and_only_metadata_is_recorded():
    asr = FakeAsr()
    service, runtime, limiter, spy = build(provider=asr)
    wav = make_wav(1.0)
    outcome = run(service, wav)
    assert outcome.text == TRANSCRIPT and outcome.language == "yo"
    assert outcome.model == "NCAIR1/Yoruba-ASR"
    assert outcome.audio_format == "wav" and outcome.audio_bytes == len(wav)
    assert outcome.duration_seconds == 1.0
    assert outcome.attribution == ASR_MODELS["yo"].required_attribution
    assert [step["name"] for step in outcome.steps] == ["validate", "asr"]
    assert all(step["status"] == "ok" for step in outcome.steps)
    assert outcome.headers["X-RateLimit-Limit"] == "10"
    assert asr.calls == [{"size": len(wav), "format": "wav", "language": "yo", "rid": "req_voice_00001"}]
    assert runtime._active == 0

    (record,) = spy.records
    assert record.outcome == "success" and record.status_code == 200 and record.error_code is None
    assert record.model == "NCAIR1/Yoruba-ASR" and record.language == "yo"
    assert record.audio_format == "wav" and record.audio_bytes == len(wav)
    assert record.audio_duration_ms == 1000 and record.transcript_chars == len(TRANSCRIPT)
    assert record.asr_ms is not None and record.validation_ms is not None
    assert record.endpoint == API_ENDPOINT and record.request_id == "req_voice_00001"
    # A record holds counts and timings, never the audio or the words.
    field_names = {field.name for field in dataclasses.fields(record)}
    assert not field_names & {"text", "transcript", "audio", "content", "body"}
    assert TRANSCRIPT not in repr(record)


def test_language_names_and_codes_both_work_and_each_uses_its_own_official_model():
    for value, code in (("yoruba", "yo"), ("Hausa", "ha"), ("ig", "ig"), ("en", "en")):
        service, *_ = build(provider=FakeAsr())
        outcome = run(service, make_wav(), language=value)
        assert outcome.language == code and outcome.model == ASR_MODELS[code].repo_id


def test_the_rate_limit_bucket_is_per_key_or_per_person_and_the_endpoint_is_recorded():
    key_id, user_id = uuid.uuid4(), uuid.uuid4()
    service, _, limiter, spy = build(provider=FakeAsr())
    run(service, make_wav(), caller=VoiceCaller(project_id=PROJECT_ID, api_key_id=key_id))
    run(
        service,
        make_wav(),
        caller=VoiceCaller(project_id=PROJECT_ID, user_id=user_id, endpoint=BROWSER_ENDPOINT),
    )
    assert limiter.keys[0][0] == f"rl:voice:key:{key_id}"
    assert limiter.keys[1][0] == f"rl:voice:user:{user_id}"
    assert limiter.keys[0][1:] == (10, 60)
    assert spy.records[1].endpoint == BROWSER_ENDPOINT
    assert spy.records[1].user_id == user_id and spy.records[1].api_key_id is None


# --- the audio is refused before the worker is used -----------------------------------------
def _refused(service, spy, asr, audio, **kwargs):
    error = fails(service, audio, **kwargs)
    assert asr.calls == []
    (record,) = spy.records
    assert record.outcome == "error" and record.status_code == error.status_code
    assert record.error_code == error.code and record.transcript_chars is None
    return error, record


def test_bytes_that_are_not_audio_are_refused_and_recorded_with_their_size():
    asr, spy = FakeAsr(), Spy()
    service, runtime, *_ = build(provider=asr, spy=spy)
    error, record = _refused(service, spy, asr, b"this is not audio at all" * 20)
    assert error.status_code in (415, 422) and error.code.startswith("audio_")
    assert record.audio_bytes == 24 * 20 and runtime._active == 0


def test_a_clip_whose_type_disagrees_with_its_content_is_refused():
    asr, spy = FakeAsr(), Spy()
    service, *_ = build(provider=asr, spy=spy)
    error, _ = _refused(service, spy, asr, make_wav(), mime="audio/mpeg")
    assert error.code == "audio_signature_mismatch"


def test_an_empty_upload_is_refused():
    asr, spy = FakeAsr(), Spy()
    service, *_ = build(provider=asr, spy=spy)
    error, record = _refused(service, spy, asr, b"")
    assert error.code == "audio_empty" and record.audio_bytes == 0


def test_a_file_over_the_size_limit_is_refused_with_413():
    asr, spy = FakeAsr(), Spy()
    service, *_ = build(settings=_settings(voice_max_upload_bytes=20_000), provider=asr, spy=spy)
    error, _ = _refused(service, spy, asr, make_wav(2.0))
    assert error.code == "audio_too_large" and error.status_code == 413


def test_a_clip_over_the_length_limit_is_refused():
    asr, spy = FakeAsr(), Spy()
    service, *_ = build(settings=_settings(voice_max_seconds=1), provider=asr, spy=spy)
    error, _ = _refused(service, spy, asr, make_wav(3.0))
    assert error.code == "audio_too_long"


def test_an_unsupported_language_is_a_422_that_is_neither_limited_nor_recorded():
    asr, spy = FakeAsr(), Spy()
    service, _, limiter, _ = build(provider=asr, spy=spy)
    error = fails(service, make_wav(), language="klingon")
    assert error.status_code == 422 and error.code == "language_unsupported"
    assert limiter.keys == [] and spy.records == [] and asr.calls == []


# --- the speech worker -----------------------------------------------------------------------
def test_with_no_worker_connected_the_answer_is_an_honest_503_and_it_is_recorded():
    spy = Spy()
    service, *_ = build(spy=spy)
    error = fails(service, make_wav())
    assert error.status_code == 503 and error.code == "provider_not_configured"
    (record,) = spy.records
    assert record.outcome == "error" and record.error_code == "provider_not_configured"
    assert record.model == "NCAIR1/Yoruba-ASR"


def test_a_worker_address_that_cannot_be_used_is_reported_as_unavailable():
    def factory(settings):
        raise ForgeError("outbound_blocked", "blocked", http_status=403)

    spy = Spy()
    service, runtime, *_ = build(settings=_settings(asr_configured=True), factory=factory, spy=spy)
    error = fails(service, make_wav())
    assert error.status_code == 503 and error.code == "provider_unavailable"
    assert runtime.error == "outbound_blocked"
    assert "blocked" not in error.message  # the cause stays in the logs, not the response


def test_when_every_slot_is_taken_the_upload_is_turned_away_quickly_with_retry_after():
    asr, spy = FakeAsr(), Spy()
    service, runtime, *_ = build(
        settings=_settings(voice_max_concurrent=1), provider=asr, spy=spy
    )
    assert runtime.try_acquire()
    error = fails(service, make_wav())
    assert error.status_code == 503 and error.code == "provider_busy"
    assert error.headers["Retry-After"] == "2" and error.headers["X-RateLimit-Limit"] == "10"
    assert asr.calls == [] and spy.records[0].error_code == "provider_busy"
    runtime.release()
    assert run(service, make_wav()).text == TRANSCRIPT


def test_a_worker_timeout_keeps_its_status_and_gives_the_slot_back():
    asr = FakeAsr(error=ForgeError("provider_timeout", "The speech worker timed out.", http_status=504))
    spy = Spy()
    service, runtime, *_ = build(provider=asr, spy=spy)
    error = fails(service, make_wav())
    assert error.status_code == 504 and error.code == "provider_timeout"
    assert runtime._active == 0
    (record,) = spy.records
    assert record.outcome == "error" and record.status_code == 504
    assert record.audio_format == "wav"  # validated before the worker was called


def test_an_unexpected_crash_is_a_generic_500_that_does_not_leak_the_cause():
    asr = FakeAsr(error=RuntimeError("secret internal detail /srv/models/token"))
    service, runtime, _, spy = build(provider=asr)
    error = fails(service, make_wav())
    assert error.status_code == 500 and error.code == "internal_error"
    assert "secret" not in error.message and "/srv" not in error.message
    assert runtime._active == 0 and spy.records[0].error_code == "internal_error"


# --- limits and bookkeeping ------------------------------------------------------------------
def test_a_caller_over_the_rate_limit_is_stopped_before_anything_else_happens():
    limited = RateLimited("slow down", code="rate_limited", headers={"Retry-After": "30"})
    asr, spy = FakeAsr(), Spy()
    service, *_ = build(provider=asr, limiter=FakeLimiter(error=limited), spy=spy)
    error = fails(service, make_wav())
    assert error.status_code == 429 and error.headers["Retry-After"] == "30"
    assert asr.calls == [] and spy.records == []


def test_a_failing_recorder_never_fails_the_request():
    service, *_ = build(provider=FakeAsr(), spy=Spy(fail=True))
    assert run(service, make_wav()).text == TRANSCRIPT


def test_rate_limit_headers_are_on_errors_too():
    service, *_ = build()
    error = fails(service, make_wav())
    assert error.headers["X-RateLimit-Remaining"] == "9"
