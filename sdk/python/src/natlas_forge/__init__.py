"""Typed Python client for N-ATLAS Forge.

    from natlas_forge import Forge

    forge = Forge(base_url="https://forge.example.com", api_key="nfk_...")
    reply = forge.chat.create([{"role": "user", "content": "Say hello"}], language="yo")
    print(reply.text, reply.request_id)
"""

from ._version import __version__
from .client import AsyncForge, Forge
from .errors import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    ForgeError,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
)
from .results import (
    ChatCompletion,
    ChatMessage,
    Model,
    ModelList,
    RateLimit,
    Transcription,
    TranscriptionAudio,
    Usage,
)

__all__ = [
    "APIConnectionError",
    "APIStatusError",
    "APITimeoutError",
    "AsyncForge",
    "AuthenticationError",
    "ChatCompletion",
    "ChatMessage",
    "Forge",
    "ForgeError",
    "InvalidRequestError",
    "Model",
    "ModelList",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimit",
    "RateLimitError",
    "ServerError",
    "Transcription",
    "TranscriptionAudio",
    "Usage",
    "__version__",
]
