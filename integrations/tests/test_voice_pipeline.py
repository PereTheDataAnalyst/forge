import json
import unittest

from forge_integrations.errors import ErrorCode, ForgeError
from forge_integrations.voice.pipeline import MAX_TRANSCRIPT_TO_LLM_CHARS, VoiceFailure, VoicePipeline

from .helpers import FakeAsrProvider, FakeGateway, RecordingObserver, make_wav


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 0.01
        return self.now


def pipeline(asr=None, gateway=None, observer=None, **kwargs):
    return VoicePipeline(
        asr or FakeAsrProvider(),
        gateway=gateway,
        observer=observer,
        perf=Clock(),
        new_id=lambda: "req_fixed",
        **kwargs,
    )


class TranscribeTests(unittest.IsolatedAsyncioTestCase):
    async def test_validate_then_asr(self):
        asr = FakeAsrProvider(text="bawo ni", duration=2.0)
        result = await pipeline(asr).transcribe(make_wav(2.0), language="yo", declared_mime="audio/wav", project_id="p1")
        self.assertEqual(result.request_id, "req_fixed")
        self.assertEqual([s.name for s in result.steps], ["validate", "asr"])
        self.assertTrue(all(s.status == "ok" for s in result.steps))
        self.assertIsNone(result.response)
        self.assertEqual(asr.calls, [{"size": len(make_wav(2.0)), "format": "wav", "language": "yo", "request_id": "req_fixed"}])
        payload = result.to_dict()
        self.assertEqual(payload["transcription"], {"text": "bawo ni", "model": "NCAIR1/Yoruba-ASR", "latency_ms": 12.5})
        self.assertEqual(payload["audio"]["format"], "wav")
        self.assertEqual(payload["audio"]["duration_seconds"], 2.0)
        self.assertEqual(len(payload["audio"]["sha256"]), 64)
        self.assertIsNone(payload["response"])
        json.dumps(payload)  # serialisable

    async def test_language_names_are_accepted(self):
        asr = FakeAsrProvider()
        result = await pipeline(asr).transcribe(make_wav(), language="Hausa")
        self.assertEqual(result.language, "ha")
        self.assertEqual(asr.calls[0]["language"], "ha")

    async def test_validation_failures_stop_before_the_asr_call_and_keep_the_steps(self):
        asr = FakeAsrProvider()
        pl = pipeline(asr)
        cases = [
            (b"", "yo", None, ErrorCode.AUDIO_EMPTY),
            (b"%PDF-1.7\n" + b"x" * 64, "yo", None, ErrorCode.AUDIO_UNSUPPORTED_TYPE),
            (make_wav(), "yo", "image/png", ErrorCode.AUDIO_SIGNATURE_MISMATCH),
            (make_wav(), "fr", None, ErrorCode.LANGUAGE_UNSUPPORTED),
            (make_wav(31.0), "yo", None, ErrorCode.AUDIO_TOO_LONG),
        ]
        for audio, language, mime, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(VoiceFailure) as caught:
                    await pl.transcribe(audio, language=language, declared_mime=mime)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.request_id, "req_fixed")
                self.assertEqual([(s.name, s.status, s.error_code) for s in caught.exception.steps], [("validate", "error", code)])
        self.assertEqual(asr.calls, [])

    async def test_size_limit_is_configurable(self):
        pl = pipeline(max_upload_bytes=1000)
        with self.assertRaises(VoiceFailure) as caught:
            await pl.transcribe(make_wav(1.0), language="yo")
        self.assertEqual(caught.exception.code, ErrorCode.AUDIO_TOO_LARGE)
        self.assertEqual(caught.exception.http_status, 413)

    async def test_asr_failure_is_recorded_and_surfaced_with_its_status(self):
        asr = FakeAsrProvider(error=ForgeError(ErrorCode.PROVIDER_UNAVAILABLE, "down", http_status=503, retryable=True))
        with self.assertRaises(VoiceFailure) as caught:
            await pipeline(asr).transcribe(make_wav(), language="ig")
        failure = caught.exception
        self.assertEqual((failure.code, failure.http_status, failure.retryable), (ErrorCode.PROVIDER_UNAVAILABLE, 503, True))
        self.assertEqual([(s.name, s.status) for s in failure.steps], [("validate", "ok"), ("asr", "error")])
        self.assertEqual(failure.steps[1].detail["model"], "NCAIR1/Igbo-ASR")


class RespondTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcribe_and_respond_calls_the_gateway_with_the_transcript(self):
        gateway = FakeGateway("Ẹ kú àárọ̀")
        asr = FakeAsrProvider(text="ẹ káàrọ̀ sir")
        result = await pipeline(asr, gateway).transcribe_and_respond(
            make_wav(), language="yo", system_prompt="You are helpful.", respond_language="yo",
            model="NCAIR1/N-ATLaS", temperature=0.1, max_tokens=200, project_id="p9",
        )
        self.assertEqual([s.name for s in result.steps], ["validate", "asr", "llm"])
        request = gateway.requests[0]
        self.assertEqual([(m.role, m.content) for m in request.messages], [("system", "You are helpful."), ("user", "ẹ káàrọ̀ sir")])
        self.assertEqual(
            (request.model, request.temperature, request.max_tokens, request.language, request.project_id, request.source, request.request_id),
            ("NCAIR1/N-ATLaS", 0.1, 200, "yo", "p9", "voice", "req_fixed"),
        )
        payload = result.to_dict()
        self.assertEqual(payload["response"]["text"], "Ẹ kú àárọ̀")
        self.assertEqual(payload["response"]["usage"], {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12})
        json.dumps(payload)

    async def test_default_response_language_follows_the_spoken_language(self):
        gateway = FakeGateway()
        await pipeline(gateway=gateway).transcribe_and_respond(make_wav(), language="ha")
        self.assertEqual(gateway.requests[0].language, "ha")
        self.assertEqual([m.role for m in gateway.requests[0].messages], ["user"])

    async def test_long_transcripts_are_trimmed_before_the_model_call(self):
        gateway = FakeGateway()
        await pipeline(FakeAsrProvider(text="a" * 10_000), gateway).transcribe_and_respond(make_wav(), language="en")
        self.assertEqual(len(gateway.requests[0].messages[-1].content), MAX_TRANSCRIPT_TO_LLM_CHARS)

    async def test_missing_gateway_is_a_clear_configuration_error(self):
        with self.assertRaises(ForgeError) as caught:
            await pipeline().transcribe_and_respond(make_wav(), language="yo")
        self.assertEqual(caught.exception.code, ErrorCode.PROVIDER_NOT_CONFIGURED)
        self.assertEqual(caught.exception.http_status, 503)

    async def test_gateway_failure_keeps_the_earlier_steps(self):
        gateway = FakeGateway(error=ForgeError(ErrorCode.PROVIDER_TIMEOUT, "slow", http_status=504, retryable=True))
        with self.assertRaises(VoiceFailure) as caught:
            await pipeline(gateway=gateway).transcribe_and_respond(make_wav(), language="yo")
        self.assertEqual([(s.name, s.status) for s in caught.exception.steps], [("validate", "ok"), ("asr", "ok"), ("llm", "error")])
        self.assertEqual(caught.exception.http_status, 504)


class ObservabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_events_cover_every_step_and_never_contain_audio_or_text(self):
        observer = RecordingObserver()
        secret_text = "SENSITIVE-TRANSCRIPT-TEXT"
        wav = make_wav(0.5)
        await pipeline(FakeAsrProvider(text=secret_text), FakeGateway(), observer).transcribe_and_respond(
            wav, language="yo", project_id="proj-1"
        )
        self.assertEqual([e["step"] for e in observer.events], ["validate", "asr", "llm"])
        blob = json.dumps(observer.events)
        self.assertNotIn(secret_text, blob)
        self.assertNotIn(wav[:32].hex(), blob)
        for event in observer.events:
            self.assertEqual(event["event"], "voice.step")
            self.assertEqual(event["request_id"], "req_fixed")
            self.assertEqual(event["project_id"], "proj-1")
            self.assertEqual(event["status"], "ok")
            self.assertGreater(event["duration_ms"], 0)

    async def test_failure_events_carry_the_error_code(self):
        observer = RecordingObserver()
        with self.assertRaises(VoiceFailure):
            await pipeline(FakeAsrProvider(error=ForgeError(ErrorCode.PROVIDER_ERROR, "boom", http_status=502)), observer=observer).transcribe(
                make_wav(), language="yo"
            )
        self.assertEqual([(e["step"], e["status"], e["error_code"]) for e in observer.events], [("validate", "ok", None), ("asr", "error", ErrorCode.PROVIDER_ERROR)])

    async def test_a_broken_observer_cannot_break_a_request(self):
        class Exploding:
            def emit(self, event):
                raise RuntimeError("logging backend is down")

        result = await pipeline(observer=Exploding()).transcribe(make_wav(), language="yo")
        self.assertEqual(result.transcription.text, "bawo ni o se wa")


if __name__ == "__main__":
    unittest.main()
