"""Unicode-aware text normalisation for Nigerian languages.

Yoruba, Igbo and Hausa text is often typed with inconsistent diacritics (tone
marks, under-dots, hooked letters) and apostrophes. ``ignore_diacritics`` folds
those differences so a check can compare meaning instead of keyboard habits.
"""

from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher

_HOOKED = {"ɓ": "b", "ɗ": "d", "ƙ": "k", "ƴ": "y", "Ɓ": "B", "Ɗ": "D", "Ƙ": "K", "Ƴ": "Y"}
_APOSTROPHES = {ord(c): None for c in "'\u2019\u02bc`\u00b4\u2018"}


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def fold_diacritics(text: str) -> str:
    """Remove combining marks and map Hausa hooked letters to their plain forms."""
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return "".join(_HOOKED.get(ch, ch) for ch in unicodedata.normalize("NFC", stripped))


def normalise(
    text: str,
    *,
    ignore_case: bool = True,
    ignore_diacritics: bool = False,
    normalize_whitespace: bool = True,
    strip_punctuation: bool = False,
) -> str:
    out = nfc(text)
    if ignore_diacritics:
        out = fold_diacritics(out).translate(_APOSTROPHES)
    if strip_punctuation:
        out = "".join(" " if unicodedata.category(ch)[0] in ("P", "S") else ch for ch in out)
    if ignore_case:
        out = out.casefold()
    if normalize_whitespace:
        out = " ".join(out.split())
    return out


def words(text: str) -> list[str]:
    """Split into word tokens. Combining marks stay attached to their letters."""
    tokens: list[str] = []
    current: list[str] = []
    for ch in text:
        if ch.isalnum() or unicodedata.category(ch).startswith("M"):
            current.append(ch)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def similarity(a: str, b: str) -> float:
    """Token-level similarity in [0, 1] (difflib ratio over case-folded word tokens)."""
    ta = words(normalise(a))
    tb = words(normalise(b))
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return SequenceMatcher(None, ta, tb, autojunk=False).ratio()
