import json
import socket
import tempfile
import time
import unittest
from pathlib import Path

from forge_integrations.errors import ConfigError, ErrorCode, ForgeError
from forge_integrations.voice.asr_client import HttpAsrProvider, build_multipart

from .helpers import (
    ScriptedServer,
    client_ssl_context,
    have_cryptography,
    make_test_pki,
    make_wav,
    server_ssl_context,
)

TOKEN = "asr-test-token-0123456789abcdefghij"
HOST = "asr.example.test"  # reserved TLD: it can never resolve, so reaching the server proves pinning


def ok_json(text="ẹ káàbọ̀", model="NCAIR1/Yoruba-ASR", duration=1.0):
    body = json.dumps({"text": text, "language": "yo", "model": model, "duration_seconds": duration, "request_id": "x"}).encode()
    return 200, {"Content-Type": "application/json"}, body


def error_json(status, code, message="rejected"):
    body = json.dumps({"error": {"code": code, "message": message, "request_id": "r"}}).encode()
    return status, {"Content-Type": "application/json"}, body


def local_provider(server: ScriptedServer, **overrides) -> HttpAsrProvider:
    options = dict(
        base_url=f"http://{HOST}:{server.port}",
        token=TOKEN,
        allowed_hosts=[HOST],
        allow_private=True,
        timeout_seconds=5.0,
        resolver=lambda host, port: ["127.0.0.1"],
    )
    options.update(overrides)
    base_url = options.pop("base_url")
    token = options.pop("token")
    return HttpAsrProvider(base_url, token, **options)


class ClientRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_and_exact_request_shape(self):
        wav = make_wav(0.2)
        with ScriptedServer(lambda req: ok_json()) as server:
            provider = local_provider(server)
            result = await provider.transcribe(audio=wav, audio_format="wav", language="yo", request_id="req-abc12345")
            self.assertEqual(len(server.requests), 1)
            request = server.requests[0]
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.path, "/v1/audio/transcriptions")
            self.assertEqual(request.header("Host"), f"{HOST}:{server.port}")
            self.assertEqual(request.header("Authorization"), f"Bearer {TOKEN}")
            self.assertEqual(request.header("X-Request-ID"), "req-abc12345")
            self.assertTrue(request.header("Content-Type").startswith("multipart/form-data; boundary=forge"))
            self.assertIn(b'name="language"\r\n\r\nyo\r\n', request.body)
            self.assertIn(b'name="file"; filename="audio.wav"', request.body)
            self.assertIn(b"Content-Type: audio/wav", request.body)
            self.assertIn(wav, request.body)
        self.assertEqual(result.text, "ẹ káàbọ̀")
        self.assertEqual(result.language, "yo")
        self.assertEqual(result.model, "NCAIR1/Yoruba-ASR")
        self.assertEqual(result.duration_seconds, 1.0)
        self.assertGreaterEqual(result.latency_ms, 0)
        self.assertEqual(result.request_id, "req-abc12345")

    async def test_language_names_resolve_before_any_network_call(self):
        with ScriptedServer(lambda req: ok_json()) as server:
            provider = local_provider(server)
            await provider.transcribe(audio=make_wav(0.1), audio_format="wav", language="Yoruba")
            self.assertIn(b'name="language"\r\n\r\nyo\r\n', server.requests[0].body)
            with self.assertRaises(ForgeError) as caught:
                await provider.transcribe(audio=make_wav(0.1), audio_format="wav", language="french")
            self.assertEqual(caught.exception.code, ErrorCode.LANGUAGE_UNSUPPORTED)
            with self.assertRaises(ForgeError) as caught:
                await provider.transcribe(audio=make_wav(0.1), audio_format="exe", language="yo")
            self.assertEqual(caught.exception.code, ErrorCode.AUDIO_UNSUPPORTED_TYPE)
            self.assertEqual(len(server.requests), 1)  # the two bad calls never reached the network

    async def test_no_token_means_no_authorization_header(self):
        with ScriptedServer(lambda req: ok_json()) as server:
            provider = local_provider(server, token=None)
            await provider.transcribe(audio=make_wav(0.1), audio_format="wav", language="ha")
            self.assertIsNone(server.requests[0].header("Authorization"))

    async def test_base_url_path_prefix_is_kept(self):
        with ScriptedServer(lambda req: ok_json()) as server:
            provider = local_provider(server, base_url=f"http://{HOST}:{server.port}/asr/")
            await provider.transcribe(audio=make_wav(0.1), audio_format="wav", language="ig")
            self.assertEqual(server.requests[0].path, "/asr/v1/audio/transcriptions")

    async def test_unsafe_request_ids_are_replaced(self):
        with ScriptedServer(lambda req: ok_json()) as server:
            provider = local_provider(server)
            await provider.transcribe(audio=make_wav(0.1), audio_format="wav", language="yo", request_id="a\r\nX-Evil: 1")
            sent = server.requests[0].header("X-Request-ID")
            self.assertRegex(sent, r"^[0-9a-f]{32}$")
            self.assertIsNone(server.requests[0].header("X-Evil"))

    async def test_every_supported_audio_format_gets_a_safe_generated_filename(self):
        names = {"wav": "audio.wav", "mp3": "audio.mp3", "ogg": "audio.ogg", "flac": "audio.flac", "mp4": "audio.m4a", "webm": "audio.webm"}
        with ScriptedServer(lambda req: ok_json()) as server:
            provider = local_provider(server)
            for fmt, filename in names.items():
                await provider.transcribe(audio=b"\x00" * 64, audio_format=fmt, language="en")
                self.assertIn(f'filename="{filename}"'.encode(), server.requests[-1].body)


class ClientErrorMappingTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, handler, **overrides):
        with ScriptedServer(handler) as server:
            provider = local_provider(server, **overrides)
            with self.assertRaises(ForgeError) as caught:
                await provider.transcribe(audio=make_wav(0.1), audio_format="wav", language="yo")
            return caught.exception, server

    async def test_status_mapping(self):
        table = [
            (error_json(422, ErrorCode.AUDIO_TOO_LONG, "too long"), ErrorCode.AUDIO_TOO_LONG, 422, False),
            (error_json(413, ErrorCode.AUDIO_TOO_LARGE), ErrorCode.AUDIO_TOO_LARGE, 413, False),
            (error_json(415, ErrorCode.AUDIO_UNSUPPORTED_TYPE), ErrorCode.AUDIO_UNSUPPORTED_TYPE, 415, False),
            (error_json(422, ErrorCode.AUDIO_CORRUPT), ErrorCode.AUDIO_CORRUPT, 422, False),
            (error_json(401, ErrorCode.UNAUTHORIZED), ErrorCode.PROVIDER_ERROR, 502, False),
            (error_json(403, ErrorCode.FORBIDDEN), ErrorCode.PROVIDER_ERROR, 502, False),
            (error_json(429, ErrorCode.RATE_LIMITED), ErrorCode.PROVIDER_UNAVAILABLE, 503, True),
            (error_json(503, ErrorCode.PROVIDER_UNAVAILABLE), ErrorCode.PROVIDER_UNAVAILABLE, 503, True),
            (error_json(504, ErrorCode.PROVIDER_TIMEOUT), ErrorCode.PROVIDER_TIMEOUT, 504, True),
            (error_json(500, ErrorCode.INTERNAL), ErrorCode.PROVIDER_ERROR, 502, True),
            (error_json(404, ErrorCode.NOT_FOUND), ErrorCode.PROVIDER_ERROR, 502, False),
        ]
        for response, code, status, retryable in table:
            with self.subTest(upstream=response[0], upstream_code=json.loads(response[2])["error"]["code"]):
                exc, _ = await self.call(lambda req, r=response: r)
                self.assertEqual((exc.code, exc.http_status, exc.retryable), (code, status, retryable))

    async def test_upstream_credentials_failure_is_not_blamed_on_the_caller_and_leaks_nothing(self):
        exc, _ = await self.call(lambda req: error_json(401, ErrorCode.UNAUTHORIZED, f"bad token {TOKEN}"))
        payload = json.dumps(exc.to_payload("r1"))
        self.assertNotIn(TOKEN, payload)
        self.assertEqual(exc.http_status, 502)

    async def test_passthrough_messages_are_redacted(self):
        exc, _ = await self.call(
            lambda req: error_json(422, ErrorCode.AUDIO_CORRUPT, "failed; used Bearer abcdefghijklmnop1234")
        )
        self.assertNotIn("abcdefghijklmnop1234", exc.message)

    async def test_non_json_error_body(self):
        exc, _ = await self.call(lambda req: (502, {"Content-Type": "text/html"}, b"<html>proxy error</html>"))
        self.assertEqual(exc.code, ErrorCode.PROVIDER_ERROR)
        self.assertNotIn("proxy error", exc.message)

    async def test_malformed_success_bodies(self):
        bodies = [
            b"not json",
            b"[]",
            b"{}",
            json.dumps({"text": 5, "model": "m"}).encode(),
            json.dumps({"text": "t"}).encode(),
            json.dumps({"text": "t", "model": "m", "duration_seconds": True}).encode(),
            json.dumps({"text": "t", "model": "m", "duration_seconds": "3"}).encode(),
            b"\xff\xfe\x00",
        ]
        for body in bodies:
            with self.subTest(body=body[:30]):
                exc, _ = await self.call(lambda req, b=body: (200, {"Content-Type": "application/json"}, b))
                self.assertEqual(exc.code, ErrorCode.PROVIDER_ERROR)
                self.assertEqual(exc.http_status, 502)

    async def test_oversized_response_is_a_provider_error_not_a_caller_error(self):
        exc, _ = await self.call(lambda req: (200, {"Content-Type": "application/json"}, b"{" + b" " * 5000 + b"}"), max_response_bytes=1000)
        self.assertEqual(exc.code, ErrorCode.PROVIDER_ERROR)
        self.assertEqual(exc.http_status, 502)

    async def test_redirects_are_never_followed(self):
        def handler(req):
            if req.path == "/v1/audio/transcriptions":
                return 302, {"Location": f"http://169.254.169.254/latest/meta-data/"}, b""
            return ok_json()

        exc, server = await self.call(handler)
        self.assertEqual(exc.code, ErrorCode.PROVIDER_ERROR)
        self.assertEqual(len(server.requests), 1)

    async def test_slow_service_times_out(self):
        def handler(req):
            time.sleep(1.5)
            return ok_json()

        started = time.monotonic()
        exc, _ = await self.call(handler, timeout_seconds=1.0)
        self.assertEqual(exc.code, ErrorCode.PROVIDER_TIMEOUT)
        self.assertEqual(exc.http_status, 504)
        self.assertTrue(exc.retryable)
        self.assertLess(time.monotonic() - started, 4.0)

    async def test_connection_refused_is_unavailable_and_retryable(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        provider = HttpAsrProvider(
            f"http://{HOST}:{port}", TOKEN, allowed_hosts=[HOST], allow_private=True,
            timeout_seconds=2.0, resolver=lambda h, p: ["127.0.0.1"],
        )
        with self.assertRaises(ForgeError) as caught:
            await provider.transcribe(audio=make_wav(0.1), audio_format="wav", language="yo")
        self.assertEqual(caught.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertTrue(caught.exception.retryable)


class ClientPolicyTests(unittest.IsolatedAsyncioTestCase):
    def build(self, url, **kwargs):
        options = dict(allowed_hosts=[HOST], resolver=lambda h, p: ["93.184.216.34"])
        options.update(kwargs)
        return HttpAsrProvider(url, TOKEN, **options)

    def assert_blocked(self, reason, func):
        with self.assertRaises(ForgeError) as caught:
            func()
        self.assertEqual(caught.exception.code, ErrorCode.OUTBOUND_BLOCKED)
        self.assertEqual(caught.exception.details, {"reason": reason})

    def test_construction_fails_loudly_for_unsafe_targets(self):
        self.build(f"https://{HOST}")  # the safe baseline
        self.assert_blocked("no_allowlist", lambda: self.build(f"https://{HOST}", allowed_hosts=[]))
        self.assert_blocked("host_not_allowed", lambda: self.build("https://other.example.test"))
        self.assert_blocked("insecure_http", lambda: self.build(f"http://{HOST}"))
        self.assert_blocked("blocked_address", lambda: self.build(f"https://{HOST}", resolver=lambda h, p: ["10.0.0.5"]))
        self.assert_blocked("blocked_address", lambda: self.build(f"https://{HOST}", resolver=lambda h, p: ["127.0.0.1"]))
        self.assert_blocked("credentials_in_url", lambda: self.build(f"https://user:pw@{HOST}"))
        self.assert_blocked("scheme", lambda: self.build(f"ftp://{HOST}"))

    def test_link_local_metadata_address_is_blocked_even_in_dev_mode(self):
        self.assert_blocked(
            "blocked_address",
            lambda: self.build(f"http://{HOST}", allow_private=True, resolver=lambda h, p: ["169.254.169.254"]),
        )

    def test_a_token_that_could_inject_headers_is_refused(self):
        for bad in ("abc\r\nX-Evil: 1", "has space", "tab\there", "nul\x00byte", ""):
            with self.subTest(token=repr(bad)):
                with self.assertRaises(ConfigError):
                    HttpAsrProvider(f"https://{HOST}", bad, allowed_hosts=[HOST], resolver=lambda h, p: ["93.184.216.34"])

    async def test_dns_is_rechecked_on_every_call_so_rebinding_cannot_reach_internal_hosts(self):
        answers = [["93.184.216.34"], ["10.1.2.3"]]  # first answer is public, the next one is internal
        calls = {"n": 0}

        def resolver(host, port):
            index = min(calls["n"], len(answers) - 1)
            calls["n"] += 1
            return answers[index]

        with ScriptedServer(lambda req: ok_json()) as server:
            provider = HttpAsrProvider(
                f"https://{HOST}:{server.port}", TOKEN, allowed_hosts=[HOST], resolver=resolver
            )  # passes construction with the public answer
            with self.assertRaises(ForgeError) as caught:
                await provider.transcribe(audio=make_wav(0.1), audio_format="wav", language="yo")
            self.assertEqual(caught.exception.code, ErrorCode.OUTBOUND_BLOCKED)
            self.assertEqual(server.requests, [])  # nothing was sent anywhere

    async def test_connection_goes_to_the_validated_address_not_to_a_fresh_dns_answer(self):
        # ``asr.example.test`` does not resolve. The request still arrives because the connection
        # is pinned to the address the policy check approved.
        with self.assertRaises(OSError):
            socket.getaddrinfo(HOST, 80)
        with ScriptedServer(lambda req: ok_json()) as server:
            provider = local_provider(server)
            result = await provider.transcribe(audio=make_wav(0.1), audio_format="wav", language="yo")
            self.assertEqual(result.model, "NCAIR1/Yoruba-ASR")


@unittest.skipUnless(have_cryptography(), "the cryptography package is needed to create test certificates")
class ClientTlsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pki = make_test_pki(Path(self._tmp.name), HOST)

    def tearDown(self):
        self._tmp.cleanup()

    def provider(self, server, host=HOST, context="trusted", **kwargs):
        ssl_context = client_ssl_context(self.pki) if context == "trusted" else None
        return HttpAsrProvider(
            f"https://{host}:{server.port}", TOKEN, allowed_hosts=[host], allow_private=True,
            timeout_seconds=5.0, resolver=lambda h, p: ["127.0.0.1"], ssl_context=ssl_context, **kwargs,
        )

    async def test_https_to_a_trusted_certificate_works_with_a_pinned_address(self):
        with ScriptedServer(lambda req: ok_json(), ssl_context=server_ssl_context(self.pki)) as server:
            result = await self.provider(server).transcribe(audio=make_wav(0.1), audio_format="wav", language="yo")
            self.assertEqual(result.text, "ẹ káàbọ̀")
            self.assertEqual(server.requests[0].header("Authorization"), f"Bearer {TOKEN}")

    async def test_untrusted_certificate_is_refused(self):
        with ScriptedServer(lambda req: ok_json(), ssl_context=server_ssl_context(self.pki)) as server:
            with self.assertRaises(ForgeError) as caught:
                await self.provider(server, context="system").transcribe(audio=make_wav(0.1), audio_format="wav", language="yo")
            self.assertEqual(caught.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
            self.assertEqual(server.requests, [])

    async def test_certificate_must_match_the_hostname_even_though_the_ip_is_pinned(self):
        with ScriptedServer(lambda req: ok_json(), ssl_context=server_ssl_context(self.pki)) as server:
            wrong = self.provider(server, host="other.example.test")
            with self.assertRaises(ForgeError) as caught:
                await wrong.transcribe(audio=make_wav(0.1), audio_format="wav", language="yo")
            self.assertEqual(caught.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
            self.assertEqual(server.requests, [])


class MultipartTests(unittest.TestCase):
    def test_structure(self):
        body, content_type = build_multipart({"language": "yo"}, "file", "audio.wav", "audio/wav", b"PAYLOAD")
        boundary = content_type.split("boundary=")[1]
        self.assertTrue(body.startswith(f"--{boundary}\r\n".encode()))
        self.assertTrue(body.endswith(f"\r\n--{boundary}--\r\n".encode()))
        self.assertEqual(body.count(f"--{boundary}".encode()), 3)
        self.assertIn(b"PAYLOAD", body)


if __name__ == "__main__":
    unittest.main()
