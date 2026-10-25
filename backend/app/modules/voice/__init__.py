"""Voice: speech recognition in front of the official N-ATLAS ASR models.

The platform validates the audio, calls a separate speech worker (integrations/asr-service) and
records metadata only. Audio and transcripts are never stored. See docs/voice.md.
"""

from app.modules.voice.routes import router
from app.modules.voice.runtime import register

__all__ = ["register", "router"]
