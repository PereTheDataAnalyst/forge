"""Test doubles for Voice: a real WAV file generator and a fake speech worker.

The fake exists only in tests. It never runs a model, so nothing here says anything about how
well the real N-ATLAS speech models transcribe.
"""

import io
import wave

from forge_integrations.natlas_facts import ASR_MODELS
from forge_integrations.ports import TranscriptionResult

TRANSCRIPT = "Bawo ni, a dupe pupo"


def make_wav(seconds: float = 1.0, rate: int = 16_000) -> bytes:
    """A valid mono 16-bit PCM WAV file of silence."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


class FakeAsr:
    """Stands in for the speech worker client (the library's AsrProvider port)."""

    def __init__(self, error=None, text=TRANSCRIPT):
        self.error, self.text, self.calls = error, text, []

    async def transcribe(self, *, audio, audio_format, language, request_id=None):
        self.calls.append(
            {"size": len(audio), "format": audio_format, "language": language, "rid": request_id}
        )
        if self.error:
            raise self.error
        return TranscriptionResult(
            text=self.text,
            language=language,
            model=ASR_MODELS[language].repo_id,
            duration_seconds=1.0,
            latency_ms=140.0,
            request_id=request_id,
        )
