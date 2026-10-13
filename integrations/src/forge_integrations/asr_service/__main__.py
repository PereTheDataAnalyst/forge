"""Command line for the ASR worker.

    python -m forge_integrations.asr_service serve      start the HTTP worker (default)
    python -m forge_integrations.asr_service check      verify configuration and prerequisites
    python -m forge_integrations.asr_service selftest --language yo --audio sample.wav
                                                        run one real transcription locally
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from ..errors import ConfigError, ForgeError
from ..natlas_facts import ASR_MODELS
from ..observability import JsonLogObserver
from ..security.safe_files import SecureTempStore
from ..voice.audio import suffix_for, validate_audio_upload
from ..voice.languages import resolve_asr_language
from ..voice.transcode import decode_to_pcm16k, ffmpeg_available
from .engine import TransformersWhisperEngine, runtime_importable
from .settings import AsrServiceSettings, load_asr_service_settings


def _load() -> AsrServiceSettings | None:
    try:
        return load_asr_service_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return None


def _engine(settings: AsrServiceSettings) -> TransformersWhisperEngine:
    return TransformersWhisperEngine(device=settings.device, processor_overrides=settings.overrides_dict())


def cmd_serve() -> int:
    settings = _load()
    if settings is None:
        return 2
    try:
        import uvicorn
    except ImportError:
        print('uvicorn is not installed. Run: pip install "forge-integrations[asr]"', file=sys.stderr)
        return 2
    from .app import create_app

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    app = create_app(settings, _engine(settings), observer=JsonLogObserver())
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
        access_log=False,
        server_header=False,
        proxy_headers=False,
        timeout_keep_alive=5,
    )
    return 0


def cmd_check() -> int:
    settings = _load()
    if settings is None:
        return 2
    checks = {
        "configuration": True,
        "authentication_required": settings.authentication_required,
        "ffmpeg": ffmpeg_available(settings.ffmpeg_path),
        "model_runtime_importable": runtime_importable(),
        # Presence only. The value is never read or printed.
        "hf_token_present_in_environment": bool(os.environ.get("HF_TOKEN")),
    }
    for name, ok in checks.items():
        print(f"{'ok ' if ok else 'NO '} {name}")
    print(f"listening on {settings.host}:{settings.port}; device={settings.device}; max_seconds={settings.max_seconds}")
    return 0 if checks["ffmpeg"] and checks["model_runtime_importable"] else 1


def cmd_selftest(language: str, audio_path: str) -> int:
    settings = _load()
    if settings is None:
        return 2
    path = Path(audio_path)
    if not path.is_file():
        print("The audio file does not exist.", file=sys.stderr)
        return 2
    data = path.read_bytes()
    try:
        facts = resolve_asr_language(language)
        validated = validate_audio_upload(data, None, max_bytes=settings.max_upload_bytes, max_seconds=settings.max_seconds)
        store = SecureTempStore(settings.temp_dir)
        started = time.perf_counter()
        with store.write(data, suffix_for(validated.format)) as temp_path:
            decoded = decode_to_pcm16k(
                temp_path,
                validated.format,
                max_seconds=settings.max_seconds,
                timeout_seconds=settings.decode_timeout_seconds,
                ffmpeg_path=settings.ffmpeg_path,
            )
        outcome = _engine(settings).transcribe(decoded.pcm_s16le, facts.language)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    except ForgeError as exc:
        print(json.dumps(exc.to_payload(None), ensure_ascii=False, indent=2))
        return 1
    print(
        json.dumps(
            {
                "language": facts.language,
                "model": outcome.model_repo_id,
                "audio_seconds": decoded.duration_seconds,
                "elapsed_ms_including_model_load": elapsed_ms,
                "text": outcome.text,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forge-asr-service", description="Forge worker for the official N-ATLAS ASR models.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="start the HTTP worker (default)")
    sub.add_parser("check", help="verify configuration and prerequisites")
    selftest = sub.add_parser("selftest", help="transcribe one local audio file")
    selftest.add_argument("--language", required=True, choices=sorted(ASR_MODELS))
    selftest.add_argument("--audio", required=True, help="path to a wav, mp3, ogg, flac, m4a or webm file of at most 30 seconds")
    args = parser.parse_args(argv)
    if args.command in (None, "serve"):
        return cmd_serve()
    if args.command == "check":
        return cmd_check()
    return cmd_selftest(args.language, args.audio)


if __name__ == "__main__":
    raise SystemExit(main())
