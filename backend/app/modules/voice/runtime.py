"""Process-wide Voice state: the speech worker client and a limit on parallel uploads.

Nothing here talks to the network when the application starts. The worker client is built on
the first request that needs it, so the platform starts even if the worker is down or not yet
deployed, and Voice recovers by itself once the worker can be reached.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from fastapi import FastAPI
from forge_integrations.errors import ForgeError
from forge_integrations.voice import HttpAsrProvider

from app.core.logging import get_logger
from app.core.scopes import register_scope

if TYPE_CHECKING:
    from app.core.config import Settings

SCOPE_ASR_TRANSCRIBE = "asr.transcribe"
# A request body is exactly the audio file. The allowance lets the pipeline answer a file that is
# slightly too large with its own clear message instead of a generic body-size error.
BODY_ALLOWANCE_BYTES = 64 * 1024

log = get_logger("forge.voice")


def build_provider(settings: Settings) -> HttpAsrProvider:
    """The library's HTTP client for the speech worker, built from platform settings.

    The host in ASR_SERVICE_URL is always allowed. Private and loopback addresses, and plain
    http to a remote host, stay blocked unless the owner switched them on.
    """
    assert settings.asr_service_url is not None and settings.asr_service_token is not None
    host = (urlsplit(settings.asr_service_url).hostname or "").lower()
    extra = {item.strip().lower() for item in settings.asr_allowed_hosts.split(",") if item.strip()}
    return HttpAsrProvider(
        settings.asr_service_url,
        settings.asr_service_token.get_secret_value(),
        allowed_hosts=sorted({host} | extra),
        allow_private=settings.asr_allow_private_network,
        allow_insecure_http=settings.asr_allow_plaintext_remote,
        timeout_seconds=settings.asr_timeout_seconds,
    )


class VoiceRuntime:
    """Holds the speech worker client and counts uploads in flight."""

    def __init__(
        self,
        settings: Settings,
        *,
        factory: Callable[[Settings], Any] = build_provider,
    ) -> None:
        self._settings = settings
        self._factory = factory
        self._provider: Any = None
        self._error: str | None = None
        self._active = 0

    @property
    def configured(self) -> bool:
        return self._provider is not None or self._settings.asr_configured

    @property
    def error(self) -> str | None:
        """The code of the last failure to build the worker client, or None."""
        return self._error

    def set_provider(self, provider: Any) -> None:
        """Use this ASR provider instead of building one. For tests and local development."""
        self._provider = provider
        self._error = None

    async def get_provider(self) -> Any:
        """The ASR provider, or None when no worker is configured or its address is unusable."""
        if self._provider is not None:
            return self._provider
        if not self._settings.asr_configured:
            return None
        try:
            # Building the client resolves the worker's host name, which can block.
            self._provider = await asyncio.to_thread(self._factory, self._settings)
        except ForgeError as exc:
            self._error = exc.code
        except ValueError:
            self._error = "invalid_configuration"
        else:
            self._error = None
            return self._provider
        log.error("voice.worker_unusable", extra={"voice_error": self._error})
        return None

    def try_acquire(self) -> bool:
        """Take a slot for one upload. False when the limit on parallel uploads is reached."""
        if self._active >= self._settings.voice_max_concurrent:
            return False
        self._active += 1
        return True

    def release(self) -> None:
        self._active = max(0, self._active - 1)


def register(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    register_scope(SCOPE_ASR_TRANSCRIBE)
    limit = settings.voice_max_upload_bytes + BODY_ALLOWANCE_BYTES
    app.state.body_limit_overrides["/api/v1/audio"] = limit
    app.state.body_limit_overrides["/api/v1/voice"] = limit
    app.state.voice = VoiceRuntime(settings)
