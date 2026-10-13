"""Audio upload validation.

Never trusts file extensions or client-declared types. The format is decided from
file signatures, the declared MIME type must agree with it, size is bounded, and for
WAV the duration is computed from the header. Compressed formats are decoded and
their duration enforced by the ASR service (see ``transcode.py``).
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from enum import Enum

from ..errors import ErrorCode, ForgeError


class AudioFormat(str, Enum):
    WAV = "wav"
    MP3 = "mp3"
    OGG = "ogg"
    FLAC = "flac"
    MP4 = "mp4"
    WEBM = "webm"


SUFFIX = {
    AudioFormat.WAV: ".wav",
    AudioFormat.MP3: ".mp3",
    AudioFormat.OGG: ".ogg",
    AudioFormat.FLAC: ".flac",
    AudioFormat.MP4: ".m4a",
    AudioFormat.WEBM: ".webm",
}

CANONICAL_MIME = {
    AudioFormat.WAV: "audio/wav",
    AudioFormat.MP3: "audio/mpeg",
    AudioFormat.OGG: "audio/ogg",
    AudioFormat.FLAC: "audio/flac",
    AudioFormat.MP4: "audio/mp4",
    AudioFormat.WEBM: "audio/webm",
}

ALLOWED_DECLARED_MIME: dict[AudioFormat, frozenset[str]] = {
    AudioFormat.WAV: frozenset({"audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"}),
    AudioFormat.MP3: frozenset({"audio/mpeg", "audio/mp3", "audio/mpeg3", "audio/x-mpeg-3"}),
    AudioFormat.OGG: frozenset({"audio/ogg", "application/ogg", "audio/opus"}),
    AudioFormat.FLAC: frozenset({"audio/flac", "audio/x-flac"}),
    AudioFormat.MP4: frozenset({"audio/mp4", "audio/x-m4a", "audio/m4a", "audio/aac"}),
    AudioFormat.WEBM: frozenset({"audio/webm", "video/webm"}),
}
GENERIC_MIME = frozenset({"", "application/octet-stream", "binary/octet-stream"})

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_SECONDS = 30


def suffix_for(fmt: AudioFormat) -> str:
    return SUFFIX[fmt]


def _looks_like_mp3(d: bytes) -> bool:
    if d[:3] == b"ID3":
        return len(d) >= 10 and d[3] in (2, 3, 4) and all(b < 0x80 for b in d[6:10])
    if len(d) >= 4 and d[0] == 0xFF and (d[1] & 0xE0) == 0xE0:
        version = (d[1] >> 3) & 0x3
        layer = (d[1] >> 1) & 0x3
        bitrate = d[2] >> 4
        rate = (d[2] >> 2) & 0x3
        return version != 1 and layer != 0 and bitrate != 15 and rate != 3
    return False


def sniff_audio_format(data: bytes) -> AudioFormat | None:
    """Identify the container from its signature. Returns ``None`` if not a supported format."""
    if len(data) < 12:
        return None
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return AudioFormat.WAV
    if data[:4] == b"OggS":
        return AudioFormat.OGG
    if data[:4] == b"fLaC":
        return AudioFormat.FLAC
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return AudioFormat.WEBM
    if data[4:8] == b"ftyp" and 8 <= int.from_bytes(data[:4], "big") <= 4096:
        return AudioFormat.MP4
    if _looks_like_mp3(data):
        return AudioFormat.MP3
    return None


@dataclass(frozen=True)
class WavInfo:
    channels: int
    sample_rate: int
    bits_per_sample: int
    format_tag: int
    duration_seconds: float


def parse_wav(data: bytes) -> WavInfo | None:
    """Parse the RIFF header. Returns ``None`` when the structure is not a usable WAV."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    pos = 12
    fmt: tuple[int, ...] | None = None
    data_size: int | None = None
    for _ in range(64):  # bounded chunk walk
        if pos + 8 > len(data):
            break
        chunk_id = data[pos : pos + 4]
        size = int.from_bytes(data[pos + 4 : pos + 8], "little")
        body = pos + 8
        if chunk_id == b"fmt ":
            if size < 16 or body + 16 > len(data):
                return None
            fmt = struct.unpack_from("<HHIIHH", data, body)
        elif chunk_id == b"data":
            available = len(data) - body
            # Streaming writers use 0 or 0xFFFFFFFF; truncated files claim more than exists.
            data_size = available if size in (0, 0xFFFFFFFF) or size > available else size
            break
        pos = body + size + (size & 1)
    if fmt is None or data_size is None:
        return None
    tag, channels, rate, byte_rate, block_align, bits = fmt
    if not 1 <= channels <= 8 or not 1000 <= rate <= 384_000:
        return None
    bytes_per_second = rate * block_align if block_align > 0 else byte_rate
    if bytes_per_second <= 0:
        return None
    return WavInfo(channels, rate, bits, tag, data_size / bytes_per_second)


@dataclass(frozen=True)
class ValidatedAudio:
    format: AudioFormat
    size_bytes: int
    sha256: str
    duration_seconds: float | None  # known up front only for WAV
    declared_mime: str | None


def _base_mime(declared: str | None) -> str:
    return (declared or "").split(";", 1)[0].strip().lower()


def validate_audio_upload(
    data: bytes,
    declared_mime: str | None = None,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_seconds: int = DEFAULT_MAX_SECONDS,
) -> ValidatedAudio:
    if not data:
        raise ForgeError(ErrorCode.AUDIO_EMPTY, "The audio upload is empty.", http_status=422)
    if len(data) > max_bytes:
        raise ForgeError(
            ErrorCode.AUDIO_TOO_LARGE,
            f"The audio upload exceeds the {max_bytes} byte limit.",
            http_status=413,
        )
    fmt = sniff_audio_format(data)
    if fmt is None:
        raise ForgeError(
            ErrorCode.AUDIO_UNSUPPORTED_TYPE,
            "Unsupported audio format. Supported: wav, mp3, ogg, flac, mp4/m4a, webm.",
            http_status=415,
        )
    declared = _base_mime(declared_mime)
    if declared not in GENERIC_MIME and declared not in ALLOWED_DECLARED_MIME[fmt]:
        raise ForgeError(
            ErrorCode.AUDIO_SIGNATURE_MISMATCH,
            "The declared content type does not match the audio data.",
            http_status=415,
        )
    duration: float | None = None
    if fmt is AudioFormat.WAV:
        info = parse_wav(data)
        if info is None:
            raise ForgeError(ErrorCode.AUDIO_CORRUPT, "The WAV file is malformed.", http_status=422)
        duration = round(info.duration_seconds, 3)
        if info.duration_seconds > max_seconds + 0.001:
            raise ForgeError(
                ErrorCode.AUDIO_TOO_LONG,
                f"The audio is longer than the {max_seconds} second limit.",
                http_status=422,
                details={"max_seconds": max_seconds},
            )
    return ValidatedAudio(
        format=fmt,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        duration_seconds=duration,
        declared_mime=declared or None,
    )
