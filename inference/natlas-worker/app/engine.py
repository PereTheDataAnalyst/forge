"""Runs the official N-ATLaS model with Transformers.

This follows the usage published in the model card: the Llama-3 chat template rendered with
`date_string`, then `generate()` with explicit sampling parameters. Nothing here fabricates
output. If the model is not loaded the engine reports its state and refuses to generate.

Prompts and generated text are never logged.
"""

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from app.config import WorkerSettings

log = logging.getLogger("natlas.engine")


class EngineState(str, Enum):
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"


class ContextLengthExceeded(Exception):
    def __init__(self, prompt_tokens: int, requested_new_tokens: int, limit: int) -> None:
        super().__init__("context length exceeded")
        self.prompt_tokens = prompt_tokens
        self.requested_new_tokens = requested_new_tokens
        self.limit = limit


@dataclass(frozen=True, slots=True)
class GenerationOutput:
    text: str
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    generation_ms: int


class NAtlasEngine:
    def __init__(self, settings: WorkerSettings) -> None:
        self._settings = settings
        self._state = EngineState.LOADING
        self._error: str | None = None
        self._tokenizer: Any = None
        self._model: Any = None
        self._eos_ids: set[int] = set()
        self._generation_lock = threading.Lock()
        self._device_label = "unknown"
        self._dtype_label = "unknown"
        self._versions: dict[str, str] = {}

    @property
    def state(self) -> EngineState:
        return self._state

    def start_loading(self) -> None:
        """Load the model in a background thread so the API can answer health probes meanwhile."""
        threading.Thread(target=self._load, name="natlas-model-loader", daemon=True).start()

    def _load(self) -> None:
        started = time.perf_counter()
        try:
            import torch
            import transformers
            from transformers import AutoModelForCausalLM, AutoTokenizer

            model_id = self._settings.model_id
            hf_token = os.environ.get("HF_TOKEN") or None
            dtype = self._resolve_dtype(torch)
            log.info("model.loading", extra={"model_id": model_id, "dtype": str(dtype)})

            tokenizer = AutoTokenizer.from_pretrained(model_id, token=hf_token)
            load_kwargs: dict[str, Any] = {"device_map": self._settings.device_map, "token": hf_token}
            try:
                model = AutoModelForCausalLM.from_pretrained(model_id, dtype=dtype, **load_kwargs)
            except TypeError:
                # Older Transformers releases call this argument torch_dtype.
                model = AutoModelForCausalLM.from_pretrained(
                    model_id, torch_dtype=dtype, **load_kwargs
                )
            model.eval()

            eos_ids: set[int] = set()
            configured = getattr(model.generation_config, "eos_token_id", None)
            if isinstance(configured, int):
                eos_ids.add(configured)
            elif isinstance(configured, (list, tuple)):
                eos_ids.update(int(item) for item in configured)
            if tokenizer.eos_token_id is not None:
                eos_ids.add(int(tokenizer.eos_token_id))
            end_of_turn = tokenizer.convert_tokens_to_ids("<|eot_id|>")
            if isinstance(end_of_turn, int) and end_of_turn >= 0 and end_of_turn != tokenizer.unk_token_id:
                eos_ids.add(end_of_turn)

            self._tokenizer = tokenizer
            self._model = model
            self._eos_ids = eos_ids
            self._device_label = str(getattr(model, "device", "unknown"))
            self._dtype_label = str(next(model.parameters()).dtype).replace("torch.", "")
            self._versions = {"torch": torch.__version__, "transformers": transformers.__version__}
            self._state = EngineState.READY
            log.info(
                "model.ready",
                extra={
                    "seconds": round(time.perf_counter() - started, 1),
                    "device": self._device_label,
                    "dtype": self._dtype_label,
                },
            )
        except Exception as exc:
            self._state = EngineState.FAILED
            self._error = f"{type(exc).__name__}: {str(exc)[:200]}"
            log.exception("model.load_failed")

    def _resolve_dtype(self, torch: Any) -> Any:
        choice = self._settings.dtype
        if choice == "auto":
            return torch.float16 if torch.cuda.is_available() else torch.bfloat16
        return {"float16": torch.float16, "bfloat16": torch.bfloat16, "float32": torch.float32}[choice]

    def info(self) -> dict[str, Any]:
        return {
            "state": self._state.value,
            "model_id": self._settings.model_id,
            "device": self._device_label,
            "dtype": self._dtype_label,
            "context_limit_tokens": self._settings.context_limit_tokens,
            "versions": self._versions,
            "error": self._error,
        }

    def generate(
        self,
        messages: list[dict[str, str]],
        *,
        max_new_tokens: int,
        temperature: float,
        top_p: float,
        repetition_penalty: float,
    ) -> GenerationOutput:
        if self._state is not EngineState.READY:
            raise RuntimeError("The model is not ready.")
        import torch

        tokenizer, model = self._tokenizer, self._model
        current_date = datetime.now().strftime("%d %b %Y")
        prompt = tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False, date_string=current_date
        )
        encoded = tokenizer(prompt, return_tensors="pt", add_special_tokens=False)
        prompt_tokens = int(encoded["input_ids"].shape[1])
        limit = self._settings.context_limit_tokens
        if prompt_tokens + max_new_tokens > limit:
            raise ContextLengthExceeded(prompt_tokens, max_new_tokens, limit)
        encoded = encoded.to(model.device)

        pad_token_id = (
            tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
        )
        generation_kwargs: dict[str, Any] = {
            "max_new_tokens": max_new_tokens,
            "repetition_penalty": repetition_penalty,
            "use_cache": True,
            "pad_token_id": pad_token_id,
            "max_time": self._settings.max_generation_seconds,
        }
        if self._eos_ids:
            generation_kwargs["eos_token_id"] = sorted(self._eos_ids)
        if temperature > 0:
            generation_kwargs.update(do_sample=True, temperature=temperature, top_p=top_p)
        else:
            generation_kwargs.update(do_sample=False)

        started = time.perf_counter()
        with self._generation_lock, torch.inference_mode():
            output_ids = model.generate(**encoded, **generation_kwargs)
        generation_ms = int((time.perf_counter() - started) * 1000)

        new_ids = output_ids[0][prompt_tokens:].tolist()
        finish_reason = "length"
        if new_ids and new_ids[-1] in self._eos_ids:
            finish_reason = "stop"
            new_ids = new_ids[:-1]
        text = tokenizer.decode(new_ids, skip_special_tokens=True).strip()
        return GenerationOutput(
            text=text,
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=len(new_ids),
            generation_ms=generation_ms,
        )
