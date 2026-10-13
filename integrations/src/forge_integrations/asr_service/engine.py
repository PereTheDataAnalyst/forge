"""Inference engines for the official N-ATLAS ASR models.

``TransformersWhisperEngine`` follows the code on the official model cards: a
``WhisperProcessor`` and ``WhisperForConditionalGeneration`` pair, audio at 16 kHz,
``model.generate`` under ``torch.no_grad()`` with the model's own generation configuration,
and ``processor.batch_decode(..., skip_special_tokens=True)``. The cards set no language
token, task or generation parameter, so none is set here.

The four repositories are gated on Hugging Face. Downloading them needs an access token
from an account that accepted each model's conditions, supplied through the standard
``HF_TOKEN`` environment variable. The token is never read, stored or logged by Forge.
"""

from __future__ import annotations

import importlib.util
import threading
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from ..errors import ErrorCode, ForgeError
from ..natlas_facts import ASR_MODELS

SAMPLE_RATE = 16_000


@dataclass(frozen=True)
class EngineResult:
    text: str
    model_repo_id: str


@runtime_checkable
class AsrEngine(Protocol):
    """Blocking engine. The service calls it from a worker thread."""

    def transcribe(self, pcm_s16le: bytes, language: str) -> EngineResult: ...

    def preload(self, languages: Sequence[str]) -> None: ...

    def status(self) -> dict[str, Any]: ...


def runtime_importable() -> bool:
    """True when the model runtime can be imported. Does not import it."""
    return all(importlib.util.find_spec(name) is not None for name in ("numpy", "torch", "transformers"))


def _runtime_unavailable() -> ForgeError:
    return ForgeError(
        ErrorCode.PROVIDER_UNAVAILABLE,
        "The ASR worker is missing its model runtime (torch and transformers).",
        http_status=503,
    )


def _model_unavailable() -> ForgeError:
    return ForgeError(
        ErrorCode.PROVIDER_UNAVAILABLE,
        "The N-ATLAS ASR model could not be loaded. Confirm that the Hugging Face account behind "
        "HF_TOKEN accepted the model's conditions and that the host can reach huggingface.co.",
        http_status=503,
        retryable=True,
    )


@dataclass
class _Loaded:
    processor: Any
    model: Any
    device: str


class TransformersWhisperEngine:
    def __init__(self, *, device: str = "auto", processor_overrides: Mapping[str, str] | None = None) -> None:
        if device not in {"auto", "cpu", "cuda"}:
            raise ValueError("device must be auto, cpu or cuda")
        self._device_preference = device
        self._overrides = dict(processor_overrides or {})
        self._lock = threading.Lock()  # one load or generate at a time
        self._loaded: dict[str, _Loaded] = {}

    # -- runtime ---------------------------------------------------------------------------
    def _modules(self) -> tuple[Any, Any, Any, Any]:
        try:
            import numpy
            import torch
            from transformers import WhisperForConditionalGeneration, WhisperProcessor
        except ImportError:
            raise _runtime_unavailable() from None
        return numpy, torch, WhisperProcessor, WhisperForConditionalGeneration

    def _resolve_device(self, torch: Any) -> str:
        if self._device_preference == "auto":
            return "cuda" if torch.cuda.is_available() else "cpu"
        if self._device_preference == "cuda" and not torch.cuda.is_available():
            raise ForgeError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "ASR_DEVICE is cuda but no CUDA device is available on this host.",
                http_status=503,
            )
        return self._device_preference

    def _load_locked(self, language: str) -> _Loaded:
        existing = self._loaded.get(language)
        if existing is not None:
            return existing
        facts = ASR_MODELS[language]
        _numpy, torch, processor_cls, model_cls = self._modules()
        device = self._resolve_device(torch)
        processor_repo = self._overrides.get(language, facts.repo_id)
        try:
            processor = processor_cls.from_pretrained(processor_repo)
            model = model_cls.from_pretrained(facts.repo_id)
            model.to(device)
            model.eval()
        except (OSError, ValueError, RuntimeError):
            raise _model_unavailable() from None
        loaded = _Loaded(processor=processor, model=model, device=device)
        self._loaded[language] = loaded
        return loaded

    # -- AsrEngine ---------------------------------------------------------------------------
    def preload(self, languages: Sequence[str]) -> None:
        for language in languages:
            with self._lock:
                self._load_locked(language)

    def transcribe(self, pcm_s16le: bytes, language: str) -> EngineResult:
        if language not in ASR_MODELS:
            raise ForgeError(ErrorCode.LANGUAGE_UNSUPPORTED, "Unsupported language.", http_status=422)
        with self._lock:
            loaded = self._load_locked(language)
            numpy, torch, _p, _m = self._modules()
            audio = numpy.frombuffer(pcm_s16le, dtype="<i2").astype("float32") / 32768.0
            try:
                features = loaded.processor(audio, sampling_rate=SAMPLE_RATE, return_tensors="pt").input_features
                features = features.to(loaded.device)
                with torch.no_grad():
                    generated = loaded.model.generate(features)
                decoded = loaded.processor.batch_decode(generated, skip_special_tokens=True)
            except (RuntimeError, ValueError):
                raise ForgeError(ErrorCode.PROVIDER_ERROR, "The ASR model failed to process the audio.", http_status=502) from None
        text = decoded[0].strip() if decoded else ""
        return EngineResult(text=text, model_repo_id=ASR_MODELS[language].repo_id)

    def status(self) -> dict[str, Any]:
        return {
            "runtime": "transformers",
            "runtime_available": runtime_importable(),
            "device_preference": self._device_preference,
            "loaded_languages": sorted(self._loaded),
        }
