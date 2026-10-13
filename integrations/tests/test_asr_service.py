import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from forge_integrations.asr_service.app import MULTIPART_OVERHEAD_BYTES, create_app
from forge_integrations.asr_service.gate import WorkGate
from forge_integrations.asr_service.settings import AsrServiceSettings, load_asr_service_settings
from forge_integrations.errors import ConfigError, ErrorCode, ForgeError
from forge_integrations.voice.asr_client import HttpAsrProvider
from forge_integrations.voice.transcode import ffmpeg_available

from .helpers import (
    HAVE_FFMPEG,
    FakeEngine,
    RecordingObserver,
    asgi_request,
    ffmpeg_sample,
    make_wav,
    multipart_body,
)

TOKEN = "worker-token-0123456789-abcdefghij"
AUTH = {"authorization": f"Bearer {TOKEN}"}


def settings_for(tmp: str, **overrides) -> AsrServiceSettings:
    options = dict(token=TOKEN, temp_dir=str(Path(tmp) / "work"), decode_timeout_seconds=10.0, inference_timeout_seconds=10.0)
    options.update(overrides)
    return AsrServiceSettings(**options)


def upload(wav: bytes, language: str = "yo", *, filename="clip.wav", content_type="audio/wav", extra=()):
    parts = [("file", filename, content_type, wav), ("language", None, None, language.encode()), *extra]
    body, ctype = multipart_body(parts)
    return body, {**AUTH, "content-type": ctype}


class ServiceBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.engine = FakeEngine()
        self.observer = RecordingObserver()

    def tearDown(self):
        self._tmp.cleanup()

    def app(self, **overrides):
        engine = overrides.pop("engine", self.engine)
        return create_app(settings_for(self.tmp, **overrides), engine, observer=self.observer)

    def work_dir_files(self):
        work = Path(self.tmp) / "work"
        return list(work.iterdir()) if work.exists() else []


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg is not installed")
class TranscriptionTests(ServiceBase):
    async def test_happy_path_for_each_language(self):
        app = self.app()
        for code, repo in {
            "yo": "NCAIR1/Yoruba-ASR",
            "ha": "NCAIR1/Hausa-ASR",
            "ig": "NCAIR1/Igbo-ASR",
            "en": "NCAIR1/NigerianAccentedEnglish",
        }.items():
            with self.subTest(language=code):
                body, headers = upload(make_wav(1.0), code)
                response = await asgi_request(app, "POST", "/v1/audio/transcriptions", headers=headers, body=body)
                self.assertEqual(response.status, 200, response.body)
                payload = response.json()
                self.assertEqual((payload["language"], payload["model"], payload["text"]), (code, repo, "ẹ káàbọ̀"))
                self.assertAlmostEqual(payload["duration_seconds"], 1.0, delta=0.05)
                self.assertEqual(payload["request_id"], response.header("x-request-id"))
                self.assertEqual(response.header("cache-control"), "no-store")
        self.assertEqual([c[1] for c in self.engine.calls], ["yo", "ha", "ig", "en"])
        self.assertTrue(all(abs(size - 32000) < 400 for size, _ in self.engine.calls))  # ~1 s of 16 kHz s16 mono

    async def test_every_container_goes_through_ffmpeg_to_the_engine(self):
        app = self.app()
        cases = {"mp3": "audio/mpeg", "ogg_opus": "audio/ogg", "flac": "audio/flac", "m4a": "audio/mp4", "webm_opus": "audio/webm"}
        for kind, mime in cases.items():
            with self.subTest(kind=kind):
                body, headers = upload(ffmpeg_sample(kind), "yo", content_type=mime, filename=f"clip.{kind}")
                response = await asgi_request(app, "POST", "/v1/audio/transcriptions", headers=headers, body=body)
                self.assertEqual(response.status, 200, response.body)
        self.assertEqual(len(self.engine.calls), 5)

    async def test_uploaded_filename_is_ignored_and_temp_files_are_removed(self):
        app = self.app()
        body, headers = upload(make_wav(0.5), filename="../../etc/passwd")
        response = await asgi_request(app, "POST", "/v1/audio/transcriptions", headers=headers, body=body)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.work_dir_files(), [])

    async def test_request_id_from_the_caller_is_kept_when_well_formed(self):
        app = self.app()
        body, headers = upload(make_wav(0.5))
        response = await asgi_request(app, "POST", "/v1/audio/transcriptions", headers={**headers, "x-request-id": "req_caller_12345"}, body=body)
        self.assertEqual(response.header("x-request-id"), "req_caller_12345")
        bad = await asgi_request(app, "POST", "/v1/audio/transcriptions", headers={**headers, "x-request-id": "bad id\twith spaces"}, body=body)
        self.assertRegex(bad.header("x-request-id"), r"^req_[0-9a-f]{32}$")

    async def test_input_validation_errors(self):
        app = self.app()
        wav = make_wav(0.5)
        long_wav = make_wav(31.0, rate=8000)
        corrupt_mp3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + os.urandom(1024)
        cases = [
            ("not audio", upload(b"%PDF-1.7\n" + b"x" * 200), 415, ErrorCode.AUDIO_UNSUPPORTED_TYPE),
            ("declared type mismatch", upload(wav, content_type="image/png"), 415, ErrorCode.AUDIO_SIGNATURE_MISMATCH),
            ("empty file", upload(b""), 422, ErrorCode.AUDIO_EMPTY),
            ("unsupported language", upload(wav, "fr"), 422, ErrorCode.LANGUAGE_UNSUPPORTED),
            ("too long wav", upload(long_wav), 422, ErrorCode.AUDIO_TOO_LONG),
            ("corrupt mp3", upload(corrupt_mp3, content_type="audio/mpeg"), 422, ErrorCode.AUDIO_CORRUPT),
        ]
        for name, (body, headers), status, code in cases:
            with self.subTest(case=name):
                response = await asgi_request(app, "POST", "/v1/audio/transcriptions", headers=headers, body=body)
                self.assertEqual((response.status, response.json()["error"]["code"]), (status, code), response.body)
                self.assertEqual(response.json()["error"]["request_id"], response.header("x-request-id"))
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.work_dir_files(), [])

    async def test_missing_parts(self):
        app = self.app()
        no_file, ctype = multipart_body([("language", None, None, b"yo")])
        no_lang, ctype2 = multipart_body([("file", "a.wav", "audio/wav", make_wav(0.5))])
        field_not_file, ctype3 = multipart_body([("file", None, None, b"just text"), ("language", None, None, b"yo")])
        for name, body, content_type, code in [
            ("no file", no_file, ctype, ErrorCode.INVALID_ARGUMENT),
            ("no language", no_lang, ctype2, ErrorCode.LANGUAGE_UNSUPPORTED),
            ("file sent as a plain field", field_not_file, ctype3, ErrorCode.INVALID_ARGUMENT),
        ]:
            with self.subTest(case=name):
                response = await asgi_request(app, "POST", "/v1/audio/transcriptions", headers={**AUTH, "content-type": content_type}, body=body)
                self.assertEqual((response.status, response.json()["error"]["code"]), (422, code), response.body)

    async def test_content_type_must_be_multipart(self):
        app = self.app()
        for ctype in ("application/json", "text/plain", ""):
            with self.subTest(content_type=ctype):
                headers = {**AUTH, **({"content-type": ctype} if ctype else {})}
                response = await asgi_request(app, "POST", "/v1/audio/transcriptions", headers=headers, body=b'{"file": "x"}')
                self.assertEqual((response.status, response.json()["error"]["code"]), (415, ErrorCode.UNSUPPORTED_MEDIA_TYPE))

    async def test_multipart_abuse_is_refused_with_400(self):
        app = self.app()
        wav = make_wav(0.3)
        two_files, ctype = multipart_body([("file", "a.wav", "audio/wav", wav), ("file", "b.wav", "audio/wav", wav), ("language", None, None, b"yo")])
        huge_field, ctype2 = multipart_body([("file", "a.wav", "audio/wav", wav), ("language", None, None, b"y" * 20_000)])
        many_fields, ctype3 = multipart_body([("file", "a.wav", "audio/wav", wav)] + [(f"f{i}", None, None, b"x") for i in range(10)])
        for name, body, content_type in [("two files", two_files, ctype), ("oversized field", huge_field, ctype2), ("too many fields", many_fields, ctype3)]:
            with self.subTest(case=name):
                response = await asgi_request(app, "POST", "/v1/audio/transcriptions", headers={**AUTH, "content-type": content_type}, body=body)
                self.assertEqual((response.status, response.json()["error"]["code"]), (400, ErrorCode.INVALID_REQUEST), response.body)
        big_plain_field, ctype4 = multipart_body([("file", None, None, make_wav(0.5)), ("language", None, None, b"yo")])
        too_big = await asgi_request(app, "POST", "/v1/audio/transcriptions", headers={**AUTH, "content-type": ctype4}, body=big_plain_field)
        self.assertEqual((too_big.status, too_big.json()["error"]["code"]), (400, ErrorCode.INVALID_REQUEST))
        for name, content_type, payload in [
            ("garbage with a boundary", "multipart/form-data; boundary=nope", b"this is not multipart at all"),
            ("no boundary", "multipart/form-data", b"--x\r\n\r\n"),
        ]:
            with self.subTest(case=name):
                broken = await asgi_request(app, "POST", "/v1/audio/transcriptions", headers={**AUTH, "content-type": content_type}, body=payload)
                self.assertEqual((broken.status, broken.json()["error"]["code"]), (400, ErrorCode.INVALID_REQUEST), broken.body)
        self.assertEqual(self.engine.calls, [])


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg is not installed")
class SizeLimitTests(ServiceBase):
    async def test_declared_content_length_over_the_limit_is_refused_before_the_body_is_read(self):
        app = self.app(max_upload_bytes=50_000)
        limit = 50_000 + MULTIPART_OVERHEAD_BYTES
        headers = {**AUTH, "content-type": "multipart/form-data; boundary=x", "content-length": str(limit + 1)}
        response = await asgi_request(app, "POST", "/v1/audio/transcriptions", headers=headers, body=b"")
        self.assertEqual((response.status, response.json()["error"]["code"]), (413, ErrorCode.PAYLOAD_TOO_LARGE))
        self.assertEqual(response.header("connection"), "close")

    async def test_chunked_body_without_a_length_cannot_bypass_the_limit(self):
        app = self.app(max_upload_bytes=50_000)
        # A well-formed multipart body with a 200 kB file part, streamed in 20 kB chunks and with no
        # Content-Length, against a limit of 50 kB plus framing (about 115 kB).
        body, ctype = multipart_body([("file", "a.wav", "audio/wav", b"\x00" * 200_000), ("language", None, None, b"yo")])
        chunks = [body[i : i + 20_000] for i in range(0, len(body), 20_000)]
        headers = {**AUTH, "content-type": ctype}
        response = await asgi_request(app, "POST", "/v1/audio/transcriptions", headers=headers, chunks=chunks, send_content_length=False)
        self.assertEqual((response.status, response.json()["error"]["code"]), (413, ErrorCode.PAYLOAD_TOO_LARGE), response.body)
        self.assertEqual(self.engine.calls, [])

    async def test_audio_over_the_audio_limit_but_inside_the_framing_allowance(self):
        app = self.app(max_upload_bytes=50_000)
        body, headers = upload(make_wav(2.0, rate=16000))  # ~64 kB
        self.assertGreater(len(make_wav(2.0)), 50_000)
        self.assertLess(len(body), 50_000 + MULTIPART_OVERHEAD_BYTES)
        response = await asgi_request(app, "POST", "/v1/audio/transcriptions", headers=headers, body=body)
        self.assertEqual((response.status, response.json()["error"]["code"]), (413, ErrorCode.AUDIO_TOO_LARGE))

    async def test_invalid_content_length_header(self):
        app = self.app()
        for value in ("abc", "-5", "1e3", "12 34"):
            with self.subTest(value=value):
                response = await asgi_request(app, "POST", "/v1/audio/transcriptions", headers={**AUTH, "content-length": value}, body=b"x")
                self.assertEqual((response.status, response.json()["error"]["code"]), (400, ErrorCode.INVALID_REQUEST))


class AuthAndRoutesTests(ServiceBase):
    async def test_health_and_ready_need_no_credentials_and_reveal_nothing(self):
        app = self.app()
        health = await asgi_request(app, "GET", "/healthz")
        self.assertEqual((health.status, health.json()), (200, {"status": "ok"}))
        ready = await asgi_request(app, "GET", "/readyz")
        self.assertEqual(ready.json(), {"status": "ready"} if ffmpeg_available("ffmpeg") else {"status": "not_ready"})
        self.assertEqual(ready.status, 200 if ffmpeg_available("ffmpeg") else 503)

    async def test_readiness_waits_for_preloaded_models(self):
        engine = FakeEngine()
        app = self.app(engine=engine, preload_languages=("yo", "ha"))
        before = await asgi_request(app, "GET", "/readyz")
        self.assertEqual((before.status, before.json()), (503, {"status": "not_ready"}))
        engine.preload(["yo", "ha"])
        after = await asgi_request(app, "GET", "/readyz")
        if ffmpeg_available("ffmpeg"):
            self.assertEqual((after.status, after.json()), (200, {"status": "ready"}))

    async def test_a_runtime_that_is_missing_is_not_ready(self):
        class NoRuntime(FakeEngine):
            def status(self):
                return {"runtime_available": False, "loaded_languages": []}

        response = await asgi_request(self.app(engine=NoRuntime()), "GET", "/readyz")
        self.assertEqual(response.status, 503)

    async def test_models_endpoint_requires_auth_and_returns_verified_facts(self):
        app = self.app()
        anonymous = await asgi_request(app, "GET", "/v1/models")
        self.assertEqual(anonymous.status, 401)
        response = await asgi_request(app, "GET", "/v1/models", headers=AUTH)
        self.assertEqual(response.status, 200)
        payload = response.json()
        self.assertEqual({m["repo_id"] for m in payload["models"]}, {"NCAIR1/Hausa-ASR", "NCAIR1/Igbo-ASR", "NCAIR1/Yoruba-ASR", "NCAIR1/NigerianAccentedEnglish"})
        self.assertEqual(payload["limits"]["max_seconds"], 30)
        self.assertEqual(payload["licence"]["active_end_user_cap"], 1000)
        self.assertNotIn(TOKEN, json.dumps(payload))

    async def test_authentication_failures(self):
        app = self.app()
        body, headers = upload(make_wav(0.3))
        content_type = headers["content-type"]
        bad_values = [
            None,
            "",
            "Bearer",
            "Bearer ",
            f"Basic {TOKEN}",
            f"Token {TOKEN}",
            "Bearer wrong-token-0123456789-abcdefghij",
            "Bearer " + TOKEN[:-1],
            "Bearer " + TOKEN + "x",
            "Bearer x",
        ]
        for value in bad_values:
            with self.subTest(authorization=value):
                request_headers = {"content-type": content_type}
                if value is not None:
                    request_headers["authorization"] = value
                response = await asgi_request(app, "POST", "/v1/audio/transcriptions", headers=request_headers, body=body)
                self.assertEqual(response.status, 401)
                self.assertEqual(response.header("www-authenticate"), "Bearer")
                self.assertEqual(response.json()["error"]["code"], ErrorCode.UNAUTHORIZED)
                self.assertNotIn(TOKEN, response.body.decode())
        self.assertEqual(self.engine.calls, [])

    async def test_token_in_the_query_string_does_not_authenticate(self):
        response = await asgi_request(self.app(), "GET", "/v1/models", query=f"token={TOKEN}&access_token={TOKEN}".encode())
        self.assertEqual(response.status, 401)

    async def test_scheme_is_case_insensitive(self):
        response = await asgi_request(self.app(), "GET", "/v1/models", headers={"authorization": f"bearer {TOKEN}"})
        self.assertEqual(response.status, 200)

    async def test_authentication_happens_before_the_body_is_parsed(self):
        # A body that would otherwise be a 400 still gets a 401 first.
        response = await asgi_request(self.app(), "POST", "/v1/audio/transcriptions", headers={"content-type": "multipart/form-data; boundary=x"}, body=b"garbage")
        self.assertEqual(response.status, 401)

    async def test_unknown_routes_and_methods_use_the_error_shape(self):
        app = self.app()
        missing = await asgi_request(app, "GET", "/nope")
        self.assertEqual((missing.status, missing.json()["error"]["code"]), (404, ErrorCode.NOT_FOUND))
        wrong = await asgi_request(app, "GET", "/v1/audio/transcriptions", headers=AUTH)
        self.assertEqual((wrong.status, wrong.json()["error"]["code"]), (405, ErrorCode.METHOD_NOT_ALLOWED))
        self.assertIn("POST", wrong.header("allow") or "")

    async def test_responses_carry_hardening_headers(self):
        response = await asgi_request(self.app(), "GET", "/healthz")
        self.assertEqual(response.header("x-content-type-options"), "nosniff")
        self.assertEqual(response.header("cache-control"), "no-store")
        self.assertEqual(response.header("referrer-policy"), "no-referrer")
        self.assertRegex(response.header("x-request-id"), r"^req_[0-9a-f]{32}$")


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg is not installed")
class FailureAndCapacityTests(ServiceBase):
    async def test_engine_errors_keep_their_status(self):
        engine = FakeEngine(error=ForgeError(ErrorCode.PROVIDER_UNAVAILABLE, "model not loaded", http_status=503, retryable=True))
        body, headers = upload(make_wav(0.3))
        response = await asgi_request(self.app(engine=engine), "POST", "/v1/audio/transcriptions", headers=headers, body=body)
        self.assertEqual((response.status, response.json()["error"]["code"]), (503, ErrorCode.PROVIDER_UNAVAILABLE))
        self.assertTrue(response.json()["error"]["retryable"])
        self.assertEqual(self.work_dir_files(), [])

    async def test_unexpected_exceptions_do_not_leak_internals(self):
        engine = FakeEngine()
        engine.transcribe = lambda pcm, language: (_ for _ in ()).throw(RuntimeError(f"cuda exploded, token {TOKEN}, path /srv/models"))
        body, headers = upload(make_wav(0.3))
        response = await asgi_request(self.app(engine=engine), "POST", "/v1/audio/transcriptions", headers=headers, body=body)
        self.assertEqual((response.status, response.json()["error"]["code"]), (500, ErrorCode.INTERNAL))
        text = response.body.decode()
        self.assertNotIn("cuda", text)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn("/srv", text)
        logged = json.dumps(self.observer.events)
        self.assertNotIn(TOKEN, logged)
        self.assertIn("RuntimeError", logged)  # the type name is logged, the message is not
        self.assertNotIn("cuda exploded", logged)

    async def test_requests_beyond_capacity_are_refused_fast_and_the_bound_holds(self):
        engine = FakeEngine(delay=0.6)
        app = self.app(engine=engine, max_concurrency=1, max_waiting=0)
        body, headers = upload(make_wav(0.3))

        async def call():
            return await asgi_request(app, "POST", "/v1/audio/transcriptions", headers=headers, body=body)

        first = asyncio.create_task(call())
        await asyncio.sleep(0.25)  # the first request is now inside the engine
        second = await call()
        self.assertEqual((second.status, second.json()["error"]["code"]), (503, ErrorCode.PROVIDER_UNAVAILABLE))
        self.assertEqual(second.header("retry-after"), "5")
        self.assertTrue(second.json()["error"]["retryable"])
        self.assertEqual((await first).status, 200)
        self.assertEqual(engine.max_active, 1)

    async def test_waiting_requests_are_served_in_turn_without_exceeding_concurrency(self):
        engine = FakeEngine(delay=0.15)
        app = self.app(engine=engine, max_concurrency=1, max_waiting=4)
        body, headers = upload(make_wav(0.3))
        results = await asyncio.gather(*[asgi_request(app, "POST", "/v1/audio/transcriptions", headers=headers, body=body) for _ in range(4)])
        self.assertEqual([r.status for r in results], [200] * 4)
        self.assertEqual(engine.max_active, 1)

    async def test_timeout_is_reported_and_the_slot_is_only_freed_when_the_work_ends(self):
        # The decode timeout is generous so ffmpeg is never the thing that times out here; the
        # engine is what is slow. The gate gives up after decode + inference = 1.3 s.
        engine = FakeEngine(delay=2.5)
        app = self.app(engine=engine, max_concurrency=1, max_waiting=0, decode_timeout_seconds=1.0, inference_timeout_seconds=0.3)
        body, headers = upload(make_wav(0.3))
        timed_out = await asgi_request(app, "POST", "/v1/audio/transcriptions", headers=headers, body=body)
        self.assertEqual((timed_out.status, timed_out.json()["error"]["code"]), (504, ErrorCode.PROVIDER_TIMEOUT))
        self.assertEqual(timed_out.json()["error"]["message"], "The ASR worker did not finish in time.")
        busy = await asgi_request(app, "POST", "/v1/audio/transcriptions", headers=headers, body=body)
        self.assertEqual(busy.status, 503)  # the abandoned job still holds the only slot
        engine.delay = 0.0
        recovered = None
        for _ in range(40):  # the slot frees as soon as the abandoned job really ends
            await asyncio.sleep(0.25)
            recovered = await asgi_request(app, "POST", "/v1/audio/transcriptions", headers=headers, body=body)
            if recovered.status == 200:
                break
        self.assertEqual(recovered.status, 200)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg is not installed")
class ObservabilityTests(ServiceBase):
    async def test_events_have_facts_but_never_text_audio_or_tokens(self):
        engine = FakeEngine(text="CONFIDENTIAL-TRANSCRIPT")
        wav = make_wav(0.5)
        body, headers = upload(wav, "ig")
        await asgi_request(self.app(engine=engine), "POST", "/v1/audio/transcriptions", headers=headers, body=body)
        bad_headers = {**headers, "authorization": "Bearer wrong-token-0123456789-abcdefghij"}
        await asgi_request(self.app(engine=engine), "POST", "/v1/audio/transcriptions", headers=bad_headers, body=body)
        events = self.observer.events
        self.assertEqual([e["status"] for e in events], [200, 401])
        ok = events[0]
        self.assertEqual((ok["event"], ok["language"], ok["format"], ok["size_bytes"], ok["model"]), ("asr.request", "ig", "wav", len(wav), "NCAIR1/Igbo-ASR"))
        self.assertGreater(ok["duration_ms"], 0)
        blob = json.dumps(events)
        for forbidden in ("CONFIDENTIAL-TRANSCRIPT", TOKEN, "wrong-token", wav[:24].hex()):
            self.assertNotIn(forbidden, blob)


class WorkGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounds_and_release(self):
        gate = WorkGate(concurrency=2, max_waiting=1)
        active = {"now": 0, "max": 0}
        lock = threading.Lock()

        def work(seconds):
            with lock:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            time.sleep(seconds)
            with lock:
                active["now"] -= 1
            return "done"

        tasks = [asyncio.create_task(gate.run(work, 0.3, timeout=5)) for _ in range(3)]
        await asyncio.sleep(0.1)
        with self.assertRaises(ForgeError) as caught:
            await gate.run(work, 0.1, timeout=5)  # 2 running + 1 waiting is full
        self.assertEqual(caught.exception.http_status, 503)
        self.assertEqual(await asyncio.gather(*tasks), ["done"] * 3)
        self.assertLessEqual(active["max"], 2)
        self.assertEqual((gate.running, gate.waiting), (0, 0))
        self.assertEqual(await gate.run(work, 0.0, timeout=5), "done")

    async def test_job_exceptions_propagate_and_free_the_slot(self):
        gate = WorkGate(concurrency=1, max_waiting=0)

        def boom():
            raise ForgeError(ErrorCode.AUDIO_CORRUPT, "bad", http_status=422)

        with self.assertRaises(ForgeError):
            await gate.run(boom, timeout=5)
        self.assertEqual(await gate.run(lambda: 7, timeout=5), 7)

    async def test_invalid_construction(self):
        for args in ((0, 1), (1, -1)):
            with self.assertRaises(ValueError):
                WorkGate(*args)


class SettingsTests(unittest.TestCase):
    TOKEN = "t" * 32

    def test_defaults_require_a_token(self):
        with self.assertRaises(ConfigError):
            load_asr_service_settings({})
        settings = load_asr_service_settings({"ASR_SERVICE_TOKEN": self.TOKEN})
        self.assertEqual((settings.host, settings.port, settings.max_seconds, settings.max_concurrency), ("127.0.0.1", 8091, 30, 1))
        self.assertTrue(settings.authentication_required)
        self.assertNotIn(self.TOKEN, repr(settings))

    def test_short_tokens_are_refused(self):
        with self.assertRaises(ConfigError):
            load_asr_service_settings({"ASR_SERVICE_TOKEN": "short"})

    def test_anonymous_mode_only_on_loopback_with_an_explicit_flag(self):
        self.assertFalse(load_asr_service_settings({"ASR_ALLOW_UNAUTHENTICATED_LOOPBACK": "true"}).authentication_required)
        with self.assertRaises(ConfigError):
            load_asr_service_settings({"ASR_ALLOW_UNAUTHENTICATED_LOOPBACK": "true", "ASR_HOST": "0.0.0.0"})
        with self.assertRaises(ConfigError):
            load_asr_service_settings({"ASR_HOST": "127.0.0.1"})

    def test_values_are_validated(self):
        base = {"ASR_SERVICE_TOKEN": self.TOKEN}
        for name, value in [
            ("ASR_PORT", "70000"), ("ASR_MAX_SECONDS", "31"), ("ASR_MAX_CONCURRENCY", "0"),
            ("ASR_DEVICE", "tpu"), ("ASR_PRELOAD_LANGUAGES", "yo,fr"), ("ASR_PROCESSOR_OVERRIDES", "ha=not a repo"),
            ("ASR_PROCESSOR_OVERRIDES", "xx=owner/repo"), ("ASR_PROCESSOR_OVERRIDES", "ha"), ("ASR_MAX_UPLOAD_BYTES", "10"),
        ]:
            with self.subTest(name=name, value=value):
                with self.assertRaises(ConfigError):
                    load_asr_service_settings({**base, name: value})

    def test_overrides_and_preload(self):
        settings = load_asr_service_settings(
            {"ASR_SERVICE_TOKEN": self.TOKEN, "ASR_PRELOAD_LANGUAGES": "yo, HA", "ASR_PROCESSOR_OVERRIDES": "ha=NCAIR-NG/Hausa", "ASR_DEVICE": "CPU"}
        )
        self.assertEqual(settings.preload_languages, ("yo", "ha"))
        self.assertEqual(settings.overrides_dict(), {"ha": "NCAIR-NG/Hausa"})
        self.assertEqual(settings.device, "cpu")


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg is not installed")
class EndToEndOverRealSocketsTests(unittest.IsolatedAsyncioTestCase):
    """The real Forge client talks to the real ASR worker app over a real TCP connection."""

    def setUp(self):
        import uvicorn

        self._tmp = tempfile.TemporaryDirectory()
        self.engine = FakeEngine(text="Mo fẹ́ràn èdè Yorùbá")
        app = create_app(settings_for(self._tmp.name), self.engine)
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(self.server.started, "uvicorn did not start")
        self.port = self.server.servers[0].sockets[0].getsockname()[1]

    def tearDown(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self._tmp.cleanup()

    def provider(self, token=TOKEN):
        return HttpAsrProvider(
            f"http://asr.example.test:{self.port}", token, allowed_hosts=["asr.example.test"], allow_private=True,
            timeout_seconds=20, resolver=lambda h, p: ["127.0.0.1"],
        )

    async def test_client_to_worker_round_trip(self):
        result = await self.provider().transcribe(audio=ffmpeg_sample("mp3", seconds=2.0), audio_format="mp3", language="yo", request_id="req_e2e_00001")
        self.assertEqual(result.text, "Mo fẹ́ràn èdè Yorùbá")
        self.assertEqual(result.model, "NCAIR1/Yoruba-ASR")
        self.assertAlmostEqual(result.duration_seconds, 2.0, delta=0.2)
        self.assertEqual(self.engine.calls[0][1], "yo")

    async def test_wrong_token_is_a_provider_error_for_the_caller(self):
        with self.assertRaises(ForgeError) as caught:
            await self.provider(token="wrong-token-0123456789-abcdefghij").transcribe(audio=make_wav(0.3), audio_format="wav", language="yo")
        self.assertEqual((caught.exception.code, caught.exception.http_status), (ErrorCode.PROVIDER_ERROR, 502))
        self.assertEqual(self.engine.calls, [])

    async def test_worker_validation_errors_reach_the_caller_unchanged(self):
        with self.assertRaises(ForgeError) as caught:
            await self.provider().transcribe(audio=make_wav(31.0, rate=8000), audio_format="wav", language="yo")
        self.assertEqual((caught.exception.code, caught.exception.http_status), (ErrorCode.AUDIO_TOO_LONG, 422))


if __name__ == "__main__":
    unittest.main()
