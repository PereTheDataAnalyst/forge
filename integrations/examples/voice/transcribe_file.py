"""Transcribe one audio file with the Forge voice pipeline and the N-ATLAS ASR worker.

The worker (``python -m forge_integrations.asr_service``, see ``asr-service/README.md``) runs the
official N-ATLAS speech models. This script checks the file first (format by signature, size,
and for WAV the duration) and only then sends it. Settings come from the environment so the token
never appears in a command line:

    $env:ASR_SERVICE_URL   = "https://asr.example.com"
    $env:ASR_SERVICE_TOKEN = "<the worker token>"
    python transcribe_file.py clip.wav --language yo

Languages: ha (Hausa), ig (Igbo), yo (Yoruba), en (Nigerian-accented English).
Audio longer than 30 seconds is not supported by the models and is refused.
For a worker on your own machine (http, loopback) also set:
    $env:ALLOW_PRIVATE_NETWORK_TARGETS = "true"
    $env:ALLOW_INSECURE_HTTP_TARGETS   = "true"

The transcript goes to standard output; details go to standard error. Add --json for everything.
Exit codes: 0 success, 1 the audio or the service refused it, 2 bad usage or configuration.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Mapping, Sequence, TextIO

from forge_integrations.config import load_voice_settings
from forge_integrations.errors import ConfigError, ForgeError
from forge_integrations.voice import HttpAsrProvider, VoicePipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Transcribe an audio file with the N-ATLAS ASR worker.")
    parser.add_argument("file", help="audio file: wav, mp3, ogg, flac, m4a or webm")
    parser.add_argument("--language", required=True, choices=("ha", "ig", "yo", "en"), help="spoken language")
    parser.add_argument("--mime-type", help="optional content type; it must agree with the audio data")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    return parser


async def transcribe(path: Path, language: str, mime_type: str | None, env: Mapping[str, str]) -> dict:
    settings = load_voice_settings(env)
    if not settings.asr_service_url:
        raise ConfigError("ASR_SERVICE_URL is not set.")
    try:
        size = path.stat().st_size
    except OSError:
        raise ConfigError(f"cannot read {path.name}.") from None
    if size > settings.max_upload_bytes:  # refuse before reading a huge file into memory
        raise ForgeError("audio_too_large", f"The file is larger than the {settings.max_upload_bytes} byte limit.", http_status=413)
    data = path.read_bytes()
    provider = HttpAsrProvider(
        settings.asr_service_url,
        settings.asr_service_token,
        allowed_hosts=settings.asr_allowed_hosts,
        allow_private=settings.allow_private_targets,
        allow_insecure_http=settings.allow_insecure_http,
        timeout_seconds=settings.asr_timeout_seconds,
    )
    pipeline = VoicePipeline(provider, max_upload_bytes=settings.max_upload_bytes, max_seconds=settings.max_seconds)
    result = await pipeline.transcribe(data, language=language, declared_mime=mime_type)
    return result.to_dict()


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None, out: TextIO | None = None, err: TextIO | None = None) -> int:
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    environment = os.environ if env is None else env
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    try:
        result = asyncio.run(transcribe(Path(args.file), args.language, args.mime_type, environment))
    except ConfigError as exc:
        print(f"error: {exc}", file=err)
        return 2
    except ForgeError as exc:
        print(f"error: {exc.code}: {exc.message}", file=err)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2), file=out)
        return 0
    transcription = result["transcription"]
    print(transcription["text"], file=out)
    print(
        f"[model {transcription['model']}, {transcription['latency_ms']} ms, "
        f"audio {result['audio']['format']} {result['audio']['size_bytes']} bytes, request {result['request_id']}]",
        file=err,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
