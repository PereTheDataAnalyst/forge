"""N-ATLAS voice: audio validation, official ASR integration, observable pipeline."""

from .asr_client import HttpAsrProvider
from .audio import AudioFormat, ValidatedAudio, sniff_audio_format, validate_audio_upload
from .languages import resolve_asr_language
from .pipeline import StepRecord, VoiceFailure, VoicePipeline, VoiceResult

__all__ = [
    "AudioFormat",
    "HttpAsrProvider",
    "StepRecord",
    "ValidatedAudio",
    "VoiceFailure",
    "VoicePipeline",
    "VoiceResult",
    "resolve_asr_language",
    "sniff_audio_format",
    "validate_audio_upload",
]
