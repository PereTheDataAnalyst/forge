"""Hardened ffmpeg decode to 16 kHz mono PCM, the input the official ASR models expect.

Defences:
  * arguments are a fixed list, never a shell string, and the input path is generated;
  * the demuxer is forced from the sniffed format, so ffmpeg never auto-probes into
    playlist or network demuxers;
  * ``-protocol_whitelist file`` blocks network and other protocols inside containers;
  * input duration, output duration and output size are all capped;
  * a wall-clock timeout kills the process.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..errors import ErrorCode, ForgeError
from .audio import AudioFormat

SAMPLE_RATE = 16_000
BYTES_PER_SECOND = SAMPLE_RATE * 2  # s16le mono

_DEMUXER = {
    AudioFormat.WAV: "wav",
    AudioFormat.MP3: "mp3",
    AudioFormat.OGG: "ogg",
    AudioFormat.FLAC: "flac",
    AudioFormat.MP4: "mov",
    AudioFormat.WEBM: "matroska",
}


@dataclass(frozen=True)
class DecodedAudio:
    pcm_s16le: bytes
    duration_seconds: float
    sample_rate: int = SAMPLE_RATE


def ffmpeg_available(ffmpeg_path: str = "ffmpeg") -> bool:
    return shutil.which(ffmpeg_path) is not None


def build_ffmpeg_command(path: Path, fmt: AudioFormat, *, max_seconds: int, ffmpeg_path: str = "ffmpeg") -> list[str]:
    cap_bytes = (max_seconds + 1) * BYTES_PER_SECOND
    return [
        ffmpeg_path,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-protocol_whitelist",
        "file",
        "-f",
        _DEMUXER[fmt],
        "-t",
        str(max_seconds + 1),
        "-i",
        str(path),
        "-vn",
        "-sn",
        "-dn",
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-acodec",
        "pcm_s16le",
        "-t",
        str(max_seconds + 1),
        "-fs",
        str(cap_bytes),
        "-f",
        "s16le",
        "pipe:1",
    ]


def decode_to_pcm16k(
    path: Path,
    fmt: AudioFormat,
    *,
    max_seconds: int = 30,
    timeout_seconds: float = 30.0,
    ffmpeg_path: str = "ffmpeg",
) -> DecodedAudio:
    if not ffmpeg_available(ffmpeg_path):
        raise ForgeError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "Audio decoding is unavailable because ffmpeg is not installed on the ASR host.",
            http_status=503,
        )
    command = build_ffmpeg_command(path, fmt, max_seconds=max_seconds, ffmpeg_path=ffmpeg_path)
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, generated path, no shell
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ForgeError(ErrorCode.PROVIDER_TIMEOUT, "Audio decoding timed out.", http_status=504) from None
    except OSError:
        raise ForgeError(ErrorCode.PROVIDER_UNAVAILABLE, "Audio decoding could not be started.", http_status=503) from None

    pcm = completed.stdout
    if completed.returncode != 0 or not pcm:
        raise ForgeError(ErrorCode.AUDIO_CORRUPT, "The audio could not be decoded.", http_status=422)
    if len(pcm) % 2:
        pcm = pcm[:-1]
    duration = len(pcm) / BYTES_PER_SECOND
    if duration > max_seconds + 0.25:
        raise ForgeError(
            ErrorCode.AUDIO_TOO_LONG,
            f"The audio is longer than the {max_seconds} second limit.",
            http_status=422,
            details={"max_seconds": max_seconds},
        )
    if duration < 0.05:
        raise ForgeError(ErrorCode.AUDIO_CORRUPT, "The audio contains no decodable speech data.", http_status=422)
    return DecodedAudio(pcm_s16le=pcm, duration_seconds=round(duration, 3))
