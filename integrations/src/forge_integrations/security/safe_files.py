"""Safe temporary files and filenames.

Uploads are never written under a user-controlled name. Names are random, the
suffix comes from a fixed allowlist, files are created exclusively with mode
0600, and they are removed when the context exits, including on errors.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

ALLOWED_SUFFIXES = frozenset({".wav", ".mp3", ".ogg", ".flac", ".m4a", ".webm", ".bin"})
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


class SecureTempStore:
    def __init__(self, base_dir: str | os.PathLike[str], *, max_age_seconds: int = 900) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(self.base_dir, 0o700)
        self._root = self.base_dir.resolve()
        self.max_age_seconds = max_age_seconds

    @contextmanager
    def write(self, data: bytes, suffix: str = ".bin") -> Iterator[Path]:
        if suffix not in ALLOWED_SUFFIXES:
            raise ValueError("suffix is not in the allowlist")
        path = self._root / f"{uuid.uuid4().hex}{suffix}"
        if path.resolve().parent != self._root:  # defence in depth; names are generated
            raise ValueError("resolved path escapes the temp directory")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            yield path
        finally:
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def cleanup_stale(self, now: float | None = None) -> int:
        """Delete leftover files older than ``max_age_seconds``. Returns how many were removed."""
        current = time.time() if now is None else now
        removed = 0
        for entry in self._root.iterdir():
            try:
                if entry.is_file() and current - entry.stat().st_mtime > self.max_age_seconds:
                    entry.unlink()
                    removed += 1
            except OSError:
                continue
        return removed


def safe_download_filename(name: str, *, default: str = "export", max_len: int = 80) -> str:
    """Reduce an arbitrary label to a safe download filename component."""
    cleaned = _SAFE_NAME_RE.sub("_", name).strip("._-")
    cleaned = cleaned[:max_len]
    return cleaned or default
