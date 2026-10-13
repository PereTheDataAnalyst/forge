"""Shared test helpers.

Everything here is a labelled test double or fixture generator. None of it ships in the
package. Audio fixtures are real audio produced by Python's ``wave`` module and by ffmpeg
(when installed), so format sniffing and decoding are exercised on genuine containers.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import shutil
import ssl
import struct
import subprocess
import tempfile
import threading
import time
import wave
from dataclasses import dataclass, field
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping

from forge_integrations.errors import ForgeError
from forge_integrations.ports import (
    GenerationRequest,
    GenerationResult,
    TranscriptionResult,
    Usage,
)

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
POSIX = os.name == "posix"


# --------------------------------------------------------------------------- audio fixtures
def make_wav(seconds: float = 1.0, rate: int = 16000, channels: int = 1, freq: float = 440.0, amplitude: float = 0.3) -> bytes:
    """A real PCM16 WAV file containing a sine tone, built with the standard library."""
    frames = int(seconds * rate)
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        chunk = 4000
        for start in range(0, frames, chunk):
            count = min(chunk, frames - start)
            samples = bytearray()
            for n in range(start, start + count):
                value = int(32767 * amplitude * math.sin(2 * math.pi * freq * n / rate))
                samples += struct.pack("<h", value) * channels
            handle.writeframes(bytes(samples))
    return buffer.getvalue()


_FFMPEG_RECIPES: dict[str, tuple[list[str], str]] = {
    "mp3": (["-c:a", "libmp3lame", "-b:a", "64k"], "mp3"),
    "mp3_noid3": (["-c:a", "libmp3lame", "-b:a", "64k", "-write_id3v2", "0", "-write_xing", "0"], "mp3"),
    "ogg_vorbis": (["-c:a", "libvorbis"], "ogg"),
    "ogg_opus": (["-c:a", "libopus", "-b:a", "32k"], "ogg"),
    "flac": (["-c:a", "flac"], "flac"),
    "m4a": (["-c:a", "aac", "-b:a", "64k"], "ipod"),
    "webm_opus": (["-c:a", "libopus", "-b:a", "32k"], "webm"),
    "wav_stereo44": (["-c:a", "pcm_s16le"], "wav"),
}


@lru_cache(maxsize=None)
def ffmpeg_sample(kind: str, seconds: float = 1.0, rate: int = 44100, channels: int = 2) -> bytes:
    """Encode a sine tone with ffmpeg. Raises ``unittest.SkipTest`` when ffmpeg is missing."""
    import unittest

    if not HAVE_FFMPEG:
        raise unittest.SkipTest("ffmpeg is not installed")
    codec_args, muxer = _FFMPEG_RECIPES[kind]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "sample.bin"
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}:sample_rate={rate}",
            "-ac", str(channels), *codec_args, "-f", muxer, str(out),
        ]
        completed = subprocess.run(command, capture_output=True, timeout=60)
        if completed.returncode != 0:
            raise RuntimeError(f"ffmpeg could not build the {kind} fixture: {completed.stderr.decode()[:300]}")
        return out.read_bytes()


# --------------------------------------------------------------------------- test doubles
class RecordingObserver:
    """Observer test double that keeps every event."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))


class FakeAsrProvider:
    """AsrProvider test double. The transcript text is test data, not a model output."""

    def __init__(
        self,
        text: str = "bawo ni o se wa",
        *,
        model: str = "NCAIR1/Yoruba-ASR",
        duration: float | None = 1.0,
        error: ForgeError | None = None,
    ) -> None:
        self.text = text
        self.model = model
        self.duration = duration
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def transcribe(self, *, audio: bytes, audio_format: str, language: str, request_id: str | None = None):
        self.calls.append({"size": len(audio), "format": audio_format, "language": language, "request_id": request_id})
        if self.error is not None:
            raise self.error
        return TranscriptionResult(
            text=self.text, language=language, model=self.model, duration_seconds=self.duration, latency_ms=12.5, request_id=request_id
        )


class FakeGateway:
    """ModelGateway test double returning scripted text, never a model output."""

    def __init__(self, reply: str | Callable[[GenerationRequest], str] = "ok", *, error: ForgeError | None = None) -> None:
        self.reply = reply
        self.error = error
        self.requests: list[GenerationRequest] = []

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        text = self.reply(request) if callable(self.reply) else self.reply
        return GenerationResult(
            text=text, model=request.model or "NCAIR1/N-ATLaS", usage=Usage(5, 7, 12), latency_ms=20.0, finish_reason="stop"
        )


class FakeEngine:
    """AsrEngine test double for the ASR worker. Output is scripted test data."""

    def __init__(self, text: str = "ẹ káàbọ̀", *, error: ForgeError | None = None, delay: float = 0.0) -> None:
        self.text = text
        self.error = error
        self.delay = delay
        self.calls: list[tuple[int, str]] = []
        self.preloaded: list[str] = []
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def transcribe(self, pcm_s16le: bytes, language: str):
        from forge_integrations.asr_service.engine import EngineResult
        from forge_integrations.natlas_facts import ASR_MODELS

        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.calls.append((len(pcm_s16le), language))
            if self.delay:
                time.sleep(self.delay)
            if self.error is not None:
                raise self.error
            return EngineResult(text=self.text, model_repo_id=ASR_MODELS[language].repo_id)
        finally:
            with self._lock:
                self.active -= 1

    def preload(self, languages) -> None:
        self.preloaded.extend(languages)

    def status(self) -> dict[str, Any]:
        return {"runtime": "fake-test-engine", "runtime_available": True, "loaded_languages": sorted(set(self.preloaded))}


# --------------------------------------------------------------------------- local HTTP server
@dataclass
class RecordedRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes
    received_at: float = field(default_factory=time.monotonic)

    def header(self, name: str) -> str | None:
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return None


Handler = Callable[[RecordedRequest], "tuple[int, dict[str, str], bytes]"]


class ScriptedServer:
    """A real local HTTP(S) server on 127.0.0.1 with scripted responses, used as a fake remote service."""

    def __init__(self, handler: Handler | None = None, *, ssl_context: ssl.SSLContext | None = None) -> None:
        self.requests: list[RecordedRequest] = []
        self._handler: Handler = handler or (lambda req: (200, {"Content-Type": "application/json"}, b"{}"))
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # silence
                return

            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                recorded = RecordedRequest(self.command, self.path, {k: v for k, v in self.headers.items()}, body)
                outer.requests.append(recorded)
                status, headers, payload = outer._handler(recorded)
                self.send_response(status)
                sent = {k.lower() for k in headers}
                for key, value in headers.items():
                    self.send_header(key, value)
                if "content-length" not in sent:
                    self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except OSError:
                    pass

            do_GET = _serve
            do_POST = _serve

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        if ssl_context is not None:
            self.httpd.socket = ssl_context.wrap_socket(self.httpd.socket, server_side=True)
        self.port: int = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    def __enter__(self) -> "ScriptedServer":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(timeout=5)


# --------------------------------------------------------------------------- TLS fixtures
def have_cryptography() -> bool:
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass(frozen=True)
class TestPki:
    ca_cert: Path
    server_cert: Path
    server_key: Path

    __test__ = False  # not a test case


def make_test_pki(directory: Path, hostname: str = "asr.example.test") -> TestPki:
    """Create a throwaway CA and a server certificate for ``hostname`` using ``cryptography``."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = dt.datetime.now(dt.timezone.utc)

    def name(common: str) -> x509.Name:
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)])

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(name("Forge test CA"))
        .issuer_name(name("Forge test CA"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                key_encipherment=False, data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    server_key = ec.generate_private_key(ec.SECP256R1())
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(name(hostname))
        .issuer_name(ca_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    paths = TestPki(directory / "ca.pem", directory / "server.pem", directory / "server.key")
    paths.ca_cert.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    paths.server_cert.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    paths.server_key.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    return paths


def server_ssl_context(pki: TestPki) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(pki.server_cert), str(pki.server_key))
    return context


def client_ssl_context(pki: TestPki) -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(pki.ca_cert))
    return context


# --------------------------------------------------------------------------- ASGI test harness
@dataclass
class AsgiResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        import json

        return json.loads(self.body.decode("utf-8"))

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


async def asgi_request(
    app: Any,
    method: str,
    path: str,
    *,
    headers: Mapping[str, str] | None = None,
    body: bytes = b"",
    chunks: list[bytes] | None = None,
    client: tuple[str, int] = ("127.0.0.1", 50000),
    query: bytes = b"",
    send_content_length: bool = True,
) -> AsgiResponse:
    """Drive an ASGI app directly. No sockets, no extra dependencies."""
    import asyncio

    raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()]
    names = {k for k, _ in raw_headers}
    payload = b"".join(chunks) if chunks is not None else body
    if send_content_length and b"content-length" not in names and (payload or method in {"POST", "PUT"}):
        raw_headers.append((b"content-length", str(len(payload)).encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "root_path": "",
        "headers": raw_headers,
        "client": client,
        "server": ("testserver", 80),
    }
    pieces = list(chunks) if chunks is not None else [body]
    messages: list[dict[str, Any]] = []
    for index, piece in enumerate(pieces):
        messages.append({"type": "http.request", "body": piece, "more_body": index < len(pieces) - 1})
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    for message in messages:
        queue.put_nowait(message)

    async def receive() -> dict[str, Any]:
        if queue.empty():
            await asyncio.sleep(3600)  # a client that stays connected; the test finishes first
        return await queue.get()

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body_bytes = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    response_headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in start["headers"]}
    return AsgiResponse(start["status"], response_headers, body_bytes)


def multipart_body(parts: list[tuple[str, str | None, str | None, bytes]], boundary: str = "testboundary12345") -> tuple[bytes, str]:
    """Build multipart/form-data from (name, filename, content_type, data) tuples. Independent of the client under test."""
    out = bytearray()
    for name, filename, content_type, data in parts:
        out += f"--{boundary}\r\n".encode()
        disposition = f'Content-Disposition: form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        out += disposition.encode() + b"\r\n"
        if content_type:
            out += f"Content-Type: {content_type}\r\n".encode()
        out += b"\r\n" + data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"
