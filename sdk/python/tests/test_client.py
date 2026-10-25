"""Tests for the Python client. They talk to a real HTTP server on loopback: no mocks of the wire."""

import asyncio
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from natlas_forge import (
    APIConnectionError,
    APITimeoutError,
    AsyncForge,
    AuthenticationError,
    Forge,
    ForgeError,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    __version__,
)

KEY = "nfk_0123456789_" + "A" * 43
CHAT_BODY = {
    "request_id": "req_chat0000000001",
    "model": "NCAIR1/N-ATLaS",
    "message": {"role": "assistant", "content": "Bawo ni"},
    "finish_reason": "stop",
    "usage": {"prompt_tokens": 12, "completion_tokens": 30, "total_tokens": 42},
    "latency_ms": 1840,
}
VOICE_BODY = {
    "request_id": "req_voice000000001",
    "language": "yo",
    "model": "NCAIR1/Yoruba-ASR",
    "text": "Bawo ni",
    "audio": {"format": "wav", "size_bytes": 5, "duration_seconds": 1.0},
    "latency_ms": 900,
    "attribution": "Yoruba-ASR is developed by NCAIR",
}
MODEL_ITEM = {
    "id": "NCAIR1/N-ATLaS",
    "kind": "llm",
    "display_name": "N-ATLaS LLM",
    "architecture": "Llama-3 8B",
    "parameters": "8B",
    "languages": ["en", "ha", "ig", "yo"],
    "context_length_tokens": 8092,
    "license": "N-ATLaS Terms of Use",
    "source_url": "https://huggingface.co/NCAIR1/N-ATLaS",
    "attribution": "N-ATLaS is an initiative of NCAIR",
    "status": "available",
}


class FakeServer:
    """Answers from a script. Records every request it receives."""

    def __init__(self):
        self.script = []
        self.seen = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                outer.seen.append(
                    {"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body}
                )
                status, headers, payload, delay = outer.script.pop(0) if outer.script else (500, {}, b"{}", 0)
                if delay:
                    time.sleep(delay)
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    for name, value in headers.items():
                        self.send_header(name, value)
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            do_GET = do_POST = _handle

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def reply(self, status=200, body=None, headers=None, delay=0):
        self.script.append((status, headers or {}, {} if body is None else body, delay))

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def no_sleep(_seconds):
    pass


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer()
        self.addCleanup(self.server.close)
        self.slept = []
        self.forge = Forge(KEY, self.server.url, _sleep=self.slept.append, max_retries=2)

    # --- requests ---------------------------------------------------------------------------
    def test_chat_sends_only_what_was_given_and_authenticates(self):
        self.server.reply(200, CHAT_BODY, {"X-RateLimit-Limit": "60", "X-RateLimit-Remaining": "59", "X-RateLimit-Reset": "31"})
        reply = self.forge.chat.create(
            [{"role": "user", "content": "Ẹ kú àárọ̀"}], language="yo", max_tokens=64, temperature=0
        )
        (seen,) = self.server.seen
        self.assertEqual((seen["method"], seen["path"]), ("POST", "/api/v1/chat"))
        self.assertEqual(
            json.loads(seen["body"].decode("utf-8")),
            {"messages": [{"role": "user", "content": "Ẹ kú àárọ̀"}], "language": "yo", "max_tokens": 64, "temperature": 0},
        )
        headers = {k.lower(): v for k, v in seen["headers"].items()}
        self.assertEqual(headers["authorization"], f"Bearer {KEY}")
        self.assertTrue(headers["content-type"].startswith("application/json"))
        self.assertIn(f"natlas-forge-python/{__version__}", headers["user-agent"])
        self.assertNotIn("x-request-id", headers)
        self.assertEqual(reply.text, "Bawo ni")
        self.assertEqual(reply.request_id, "req_chat0000000001")
        self.assertEqual((reply.usage.total_tokens, reply.latency_ms, reply.finish_reason), (42, 1840, "stop"))
        self.assertEqual((reply.rate_limit.limit, reply.rate_limit.remaining, reply.rate_limit.reset_seconds), (60, 59, 31))

    def test_messages_may_be_objects_and_are_checked_locally(self):
        from natlas_forge import ChatMessage

        self.server.reply(200, CHAT_BODY)
        self.forge.chat.create([ChatMessage("user", "Hi")])
        self.assertEqual(json.loads(self.server.seen[0]["body"])["messages"], [{"role": "user", "content": "Hi"}])
        for bad in ([], "hello", [{"role": "user"}], [{"role": 1, "content": "x"}], [42]):
            with self.assertRaises((ValueError, TypeError)):
                self.forge.chat.create(bad)
        self.assertEqual(len(self.server.seen), 1)  # nothing bad was sent

    def test_a_caller_chosen_request_id_is_sent_and_checked(self):
        self.server.reply(200, CHAT_BODY)
        self.forge.chat.create([{"role": "user", "content": "Hi"}], request_id="my-trace_0001")
        self.assertEqual({k.lower(): v for k, v in self.server.seen[0]["headers"].items()}["x-request-id"], "my-trace_0001")
        for bad in ("short", "has space 12345", "x" * 65, "bad\r\nHeader: 1"):
            with self.assertRaises(ValueError):
                self.forge.chat.create([{"role": "user", "content": "Hi"}], request_id=bad)

    def test_models_list(self):
        self.server.reply(200, {"data": [MODEL_ITEM]}, {"X-Request-ID": "req_models00000001"})
        result = self.forge.models.list()
        self.assertEqual(self.server.seen[0]["method"], "GET")
        self.assertEqual(self.server.seen[0]["path"], "/api/v1/models")
        self.assertEqual(result.request_id, "req_models00000001")
        (model,) = result.data
        self.assertEqual((model.id, model.kind, model.status), ("NCAIR1/N-ATLaS", "llm", "available"))
        self.assertEqual(model.languages, ("en", "ha", "ig", "yo"))

    def test_transcribe_sends_the_audio_as_the_raw_body(self):
        self.server.reply(200, VOICE_BODY)
        result = self.forge.audio.transcribe(b"RIFFdata", language="yo", content_type="audio/wav")
        (seen,) = self.server.seen
        self.assertEqual(seen["path"], "/api/v1/audio/transcriptions?language=yo")
        self.assertEqual(seen["body"], b"RIFFdata")
        self.assertEqual({k.lower(): v for k, v in seen["headers"].items()}["content-type"], "audio/wav")
        self.assertEqual((result.text, result.language, result.request_id), ("Bawo ni", "yo", "req_voice000000001"))
        self.assertEqual((result.audio.format, result.audio.size_bytes, result.audio.duration_seconds), ("wav", 5, 1.0))
        self.assertIn("NCAIR", result.attribution)

    def test_transcribe_accepts_paths_and_file_objects_and_guesses_the_type(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "clip.MP3")
            with open(path, "wb") as handle:
                handle.write(b"ID3audio")
            self.server.reply(200, VOICE_BODY)
            self.forge.audio.transcribe(path, language="ha")
            self.server.reply(200, VOICE_BODY)
            with open(path, "rb") as handle:
                self.forge.audio.transcribe(handle, language="ha")
            self.server.reply(200, VOICE_BODY)
            self.forge.audio.transcribe(b"unknown", language="ig")
            types = [{k.lower(): v for k, v in s["headers"].items()}["content-type"] for s in self.server.seen]
            self.assertEqual(types, ["audio/mpeg", "audio/mpeg", "application/octet-stream"])
            self.assertEqual([s["body"] for s in self.server.seen], [b"ID3audio", b"ID3audio", b"unknown"])
            with self.assertRaises(TypeError):
                self.forge.audio.transcribe(12345, language="yo")
            with open(path, "r", encoding="latin-1") as text_handle:
                with self.assertRaises(TypeError):
                    self.forge.audio.transcribe(text_handle, language="yo")
            self.assertEqual(len(self.server.seen), 3)  # the bad inputs sent nothing

    def test_a_language_is_encoded_in_the_query(self):
        self.server.reply(200, VOICE_BODY)
        self.forge.audio.transcribe(b"x", language="yo&admin=1")
        self.assertEqual(self.server.seen[0]["path"], "/api/v1/audio/transcriptions?language=yo%26admin%3D1")

    # --- errors -----------------------------------------------------------------------------
    def error(self, status, code, message="Refused.", headers=None, details=None):
        body = {"error": {"code": code, "message": message, "request_id": "req_err00000000001"}}
        if details:
            body["error"]["details"] = details
        self.server.reply(status, body, headers or {})

    def test_each_status_becomes_its_own_error_type_with_the_request_id(self):
        cases = [
            (401, "invalid_api_key", AuthenticationError),
            (403, "insufficient_scope", PermissionDeniedError),
            (404, "model_not_found", NotFoundError),
            (413, "payload_too_large", InvalidRequestError),
            (422, "validation_error", InvalidRequestError),
        ]
        for status, code, kind in cases:
            self.error(status, code)
            with self.assertRaises(kind) as caught:
                self.forge.chat.create([{"role": "user", "content": "Hi"}])
            error = caught.exception
            self.assertEqual((error.status, error.code, error.request_id), (status, code, "req_err00000000001"))
            self.assertIn("req_err00000000001", str(error))
            self.assertIsInstance(error, ForgeError)

    def test_validation_details_are_kept(self):
        self.error(422, "validation_error", details=[{"field": "messages", "message": "required"}])
        with self.assertRaises(InvalidRequestError) as caught:
            self.forge.chat.create([{"role": "user", "content": "Hi"}])
        self.assertEqual(caught.exception.details, [{"field": "messages", "message": "required"}])

    def test_the_request_id_header_is_used_when_the_body_has_none(self):
        self.server.reply(502, b"<html>bad gateway</html>", {"X-Request-ID": "req_proxy0000000001"})
        forge = Forge(KEY, self.server.url, max_retries=0)
        with self.assertRaises(ServerError) as caught:
            forge.chat.create([{"role": "user", "content": "Hi"}])
        self.assertEqual(caught.exception.request_id, "req_proxy0000000001")
        self.assertEqual(caught.exception.code, None)

    def test_a_success_that_is_not_the_expected_shape_is_a_clear_error(self):
        self.server.reply(200, {"unexpected": True}, {"X-Request-ID": "req_odd000000000001"})
        with self.assertRaises(ForgeError) as caught:
            self.forge.chat.create([{"role": "user", "content": "Hi"}])
        self.assertEqual(caught.exception.code, "invalid_response")
        self.assertEqual(caught.exception.request_id, "req_odd000000000001")
        self.server.reply(200, b"not json at all")
        with self.assertRaises(ForgeError) as caught:
            self.forge.models.list()
        self.assertEqual(caught.exception.code, "invalid_response")

    # --- retries ----------------------------------------------------------------------------
    def test_a_rate_limit_is_retried_after_the_delay_the_server_asked_for(self):
        self.error(429, "rate_limited", headers={"Retry-After": "7"})
        self.server.reply(200, CHAT_BODY)
        reply = self.forge.chat.create([{"role": "user", "content": "Hi"}])
        self.assertEqual(reply.text, "Bawo ni")
        self.assertEqual(len(self.server.seen), 2)
        self.assertEqual(self.slept, [7.0])

    def test_a_long_retry_after_is_not_waited_for(self):
        self.error(429, "rate_limited", headers={"Retry-After": "120"})
        with self.assertRaises(RateLimitError) as caught:
            self.forge.chat.create([{"role": "user", "content": "Hi"}])
        self.assertEqual(caught.exception.retry_after, 120.0)
        self.assertEqual((len(self.server.seen), self.slept), (1, []))

    def test_retries_are_bounded_and_back_off(self):
        for _ in range(5):
            self.error(503, "provider_unavailable")
        with self.assertRaises(ServerError):
            self.forge.chat.create([{"role": "user", "content": "Hi"}])
        self.assertEqual(len(self.server.seen), 3)  # one try and two retries
        self.assertEqual(len(self.slept), 2)
        self.assertTrue(0.2 <= self.slept[0] <= 0.5 and 0.4 <= self.slept[1] <= 1.0, self.slept)

    def test_max_retries_zero_means_one_try(self):
        forge = Forge(KEY, self.server.url, max_retries=0, _sleep=self.slept.append)
        self.error(429, "rate_limited")
        with self.assertRaises(RateLimitError):
            forge.chat.create([{"role": "user", "content": "Hi"}])
        self.assertEqual((len(self.server.seen), self.slept), (1, []))

    def test_a_write_is_not_repeated_when_the_model_may_have_run(self):
        for status in (502, 504, 500):
            self.server.seen.clear()
            self.error(status, "provider_timeout" if status == 504 else "provider_error")
            with self.assertRaises(ServerError):
                self.forge.chat.create([{"role": "user", "content": "Hi"}])
            self.assertEqual(len(self.server.seen), 1, status)

    def test_a_read_is_repeated_on_502_and_504_too(self):
        self.error(504, "provider_timeout")
        self.server.reply(200, {"data": [MODEL_ITEM]})
        self.assertEqual(len(self.forge.models.list().data), 1)
        self.assertEqual(len(self.server.seen), 2)

    def test_a_503_that_waiting_cannot_fix_is_not_retried(self):
        self.error(503, "provider_not_configured")
        with self.assertRaises(ServerError) as caught:
            self.forge.audio.transcribe(b"x", language="yo")
        self.assertEqual(caught.exception.code, "provider_not_configured")
        self.assertEqual(len(self.server.seen), 1)

    # --- network ----------------------------------------------------------------------------
    def test_a_refused_connection_is_a_connection_error_and_a_write_may_be_retried_safely(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        forge = Forge(KEY, f"http://127.0.0.1:{port}", max_retries=2, _sleep=self.slept.append)
        with self.assertRaises(APIConnectionError) as caught:
            forge.chat.create([{"role": "user", "content": "Hi"}])
        self.assertNotIsInstance(caught.exception, APITimeoutError)
        self.assertEqual(len(self.slept), 2)  # nothing was sent, so repeating cannot do it twice
        self.assertNotIn(KEY, str(caught.exception))

    def test_a_timeout_is_not_retried_for_a_write_but_is_for_a_read(self):
        forge = Forge(KEY, self.server.url, timeout=0.2, max_retries=2, _sleep=self.slept.append)
        self.server.reply(200, CHAT_BODY, delay=0.8)
        with self.assertRaises(APITimeoutError):
            forge.chat.create([{"role": "user", "content": "Hi"}])
        self.assertEqual((len(self.server.seen), self.slept), (1, []))
        self.server.seen.clear()
        self.server.reply(200, {"data": []}, delay=0.8)
        self.server.reply(200, {"data": [MODEL_ITEM]})
        self.assertEqual(len(forge.models.list().data), 1)
        self.assertEqual(len(self.server.seen), 2)

    def test_a_redirect_is_never_followed_so_the_key_stays_put(self):
        other = FakeServer()
        self.addCleanup(other.close)
        other.reply(200, CHAT_BODY)
        self.server.reply(302, {}, {"Location": other.url + "/api/v1/chat"})
        with self.assertRaises(ForgeError) as caught:
            self.forge.chat.create([{"role": "user", "content": "Hi"}])
        self.assertEqual(caught.exception.code, "unexpected_redirect")
        self.assertEqual(other.seen, [])
        self.assertEqual(len(self.server.seen), 1)

    def test_an_oversized_response_is_refused(self):
        from natlas_forge import _transport

        self.server.reply(200, b"{" + b" " * (_transport.MAX_RESPONSE_BYTES + 10) + b"}")
        with self.assertRaises(ForgeError) as caught:
            self.forge.models.list()
        self.assertEqual(caught.exception.code, "invalid_response")

    # --- construction -----------------------------------------------------------------------
    def test_the_key_is_never_shown(self):
        self.assertNotIn(KEY, repr(self.forge))
        self.assertNotIn(KEY, repr(AsyncForge(KEY, self.server.url)))
        self.assertIn("nfk_0123", repr(self.forge))
        self.error(401, "invalid_api_key", message="The API key is not valid.")
        with self.assertRaises(AuthenticationError) as caught:
            self.forge.models.list()
        self.assertNotIn(KEY, str(caught.exception) + repr(caught.exception.__dict__))

    def test_bad_configuration_is_refused_before_anything_is_sent(self):
        env = {k: os.environ.pop(k) for k in ("FORGE_API_KEY", "FORGE_BASE_URL") if k in os.environ}
        self.addCleanup(os.environ.update, env)
        bad = [
            dict(api_key=None, base_url=self.server.url),
            dict(api_key="", base_url=self.server.url),
            dict(api_key="nfk with space", base_url=self.server.url),
            dict(api_key="nfk_abc\r\nX: y", base_url=self.server.url),
            dict(api_key=KEY, base_url=None),
            dict(api_key=KEY, base_url="ftp://example.com"),
            dict(api_key=KEY, base_url="https://user:pw@example.com"),
            dict(api_key=KEY, base_url="https://example.com?x=1"),
            dict(api_key=KEY, base_url="http://example.com"),  # plain http to a remote host
            dict(api_key=KEY, base_url=self.server.url, timeout=0),
            dict(api_key=KEY, base_url=self.server.url, max_retries=-1),
            dict(api_key=KEY, base_url=self.server.url, max_retries=99),
        ]
        for kwargs in bad:
            with self.assertRaises(ValueError, msg=str(kwargs)):
                Forge(**kwargs)
        self.assertEqual(self.server.seen, [])

    def test_plain_http_to_a_remote_host_needs_an_explicit_switch(self):
        Forge(KEY, "http://example.com", allow_insecure_http=True)
        Forge(KEY, "http://localhost:8000")
        Forge(KEY, "http://[::1]:8000")

    def test_the_base_url_is_normalised(self):
        for given, expected in (
            ("https://forge.example.com", "https://forge.example.com/api/v1"),
            ("https://forge.example.com/", "https://forge.example.com/api/v1"),
            ("https://forge.example.com/api/v1", "https://forge.example.com/api/v1"),
            ("https://forge.example.com/api/v1/", "https://forge.example.com/api/v1"),
            ("https://example.com/forge", "https://example.com/forge/api/v1"),
            ("http://127.0.0.1:8000", "http://127.0.0.1:8000/api/v1"),
        ):
            self.assertEqual(Forge(KEY, given).base_url, expected)

    def test_settings_can_come_from_the_environment(self):
        old = {k: os.environ.get(k) for k in ("FORGE_API_KEY", "FORGE_BASE_URL")}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in old.items()])
        os.environ["FORGE_API_KEY"], os.environ["FORGE_BASE_URL"] = KEY, self.server.url
        self.server.reply(200, {"data": [MODEL_ITEM]})
        self.assertEqual(len(Forge().models.list().data), 1)

    def test_a_custom_transport_replaces_the_network(self):
        from natlas_forge._transport import RawResponse

        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, dict(headers), body, timeout))
            return RawResponse(200, {"x-request-id": "req_custom0000001"}, json.dumps({"data": [MODEL_ITEM]}).encode())

        forge = Forge(KEY, "https://forge.example.com", transport=transport, timeout=5)
        result = forge.models.list()
        self.assertEqual(result.request_id, "req_custom0000001")
        self.assertEqual((calls[0][0], calls[0][1], calls[0][4]), ("GET", "https://forge.example.com/api/v1/models", 5.0))

    # --- async ------------------------------------------------------------------------------
    def test_the_async_client_has_the_same_calls_and_does_not_block_the_loop(self):
        self.server.reply(200, CHAT_BODY, delay=0.3)
        client = AsyncForge(KEY, self.server.url)

        async def scenario():
            ticks = 0

            async def ticker():
                nonlocal ticks
                while True:
                    await asyncio.sleep(0.02)
                    ticks += 1

            task = asyncio.create_task(ticker())
            reply = await client.chat.create([{"role": "user", "content": "Hi"}])
            task.cancel()
            return reply, ticks

        reply, ticks = asyncio.run(scenario())
        self.assertEqual(reply.text, "Bawo ni")
        self.assertGreater(ticks, 5)  # the loop kept running during the request
        self.server.reply(404, {"error": {"code": "model_not_found", "message": "No.", "request_id": "req_async0000000001"}})

        async def failing():
            await client.chat.create([{"role": "user", "content": "Hi"}], model="nope")

        with self.assertRaises(NotFoundError) as caught:
            asyncio.run(failing())
        self.assertEqual(caught.exception.request_id, "req_async0000000001")


if __name__ == "__main__":
    unittest.main()
