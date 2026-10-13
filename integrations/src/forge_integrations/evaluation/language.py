"""Heuristic language check for English, Hausa, Igbo and Yoruba.

This is NOT a language-identification model and it has not been validated against
a labelled corpus. It combines two transparent signals:

  1. the share of words that are common function words of each language, and
  2. orthographic markers: Yoruba ẹ ṣ, Igbo ị ụ ṅ, Hausa ɓ ɗ ƙ ƴ, and the under-dot
     ọ shared by Yoruba and Igbo, plus dense tone marks for Yoruba.

When the evidence is thin or ambiguous the result is "inconclusive" and the case is
marked for review instead of passing or failing. The word lists are small and should
be reviewed by native speakers before being relied on.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from .textnorm import fold_diacritics, nfc, words

LANGUAGE_CHECK_VERSION = "1"
MIN_WORDS = 4
MIN_BEST_SCORE = 0.10
MIN_MARGIN = 0.04

_EN = frozenset(
    "the and of that with for this are was were have has from they which their would there "
    "been will about because when what your not but all can".split()
)
# Words that are function words in more than one language are listed under each.
_HA = frozenset(
    "da kuma wannan wancan daga cikin amma domin cewa yana tana suna ita idan akwai babu kamar "
    "saboda wato yanzu wani wata masu shi ce ne sun zai ni na ya ka ma ga ko".split()
)
_YO = frozenset(
    "ati awon nitori fun yii naa tabi won pe ki bi sugbon lati nipa lori labe pelu tun sibe ohun "
    "eyi iyen nibi nibe gbogbo kan ti si mo ni ko".split()
)
_IG = frozenset(
    "bu nke ndi maka anyi ihe otu nwere ahu ebe mgbe onye ime etiti obodo isi nye ka ga gi di na ya ha ma".split()
)
_LEXICONS = {"en": _EN, "ha": _HA, "yo": _YO, "ig": _IG}

_STRONG = {"yo": "ẹṣẸṢ", "ig": "ịụṅỊỤṄ", "ha": "ɓɗƙƴƁƊƘƳ"}
_SHARED_YO_IG = "ọỌ"
_TONE_MARKS = ("\u0300", "\u0301")


@dataclass(frozen=True)
class LanguageGuess:
    language: str | None  # None means inconclusive
    reason: str
    scores: dict[str, float] = field(default_factory=dict)
    evidence: dict[str, object] = field(default_factory=dict)


def _bonus(marker_count: int) -> float:
    if marker_count <= 0:
        return 0.0
    return 0.20 if marker_count >= 3 else 0.10


def detect_language(text: str) -> LanguageGuess:
    composed = nfc(text)
    tokens = words(fold_diacritics(composed).casefold())
    n = len(tokens)
    if n < MIN_WORDS:
        return LanguageGuess(None, f"fewer than {MIN_WORDS} words, too short to judge", evidence={"word_count": n})

    scores = {lang: sum(1 for t in tokens if t in lex) / n for lang, lex in _LEXICONS.items()}
    strong = {lang: sum(composed.count(ch) for ch in chars) for lang, chars in _STRONG.items()}
    shared = sum(composed.count(ch) for ch in _SHARED_YO_IG)
    decomposed = unicodedata.normalize("NFD", composed)
    tone = sum(decomposed.count(mark) for mark in _TONE_MARKS)

    scores["ha"] += _bonus(strong["ha"])
    scores["yo"] += _bonus(strong["yo"]) + (0.05 if shared else 0.0) + (0.08 if tone >= 3 and strong["ig"] == 0 else 0.0)
    scores["ig"] += _bonus(strong["ig"]) + (0.05 if shared else 0.0)
    scores = {k: round(min(v, 1.0), 4) for k, v in scores.items()}

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (best, best_score), (second, second_score) = ranked[0], ranked[1]
    evidence = {
        "word_count": n,
        "strong_marker_counts": strong,
        "shared_under_dot_o": shared,
        "tone_mark_count": tone,
        "method_version": LANGUAGE_CHECK_VERSION,
    }
    if best_score < MIN_BEST_SCORE:
        return LanguageGuess(None, "no clear language signal", scores, evidence)
    if best_score - second_score < MIN_MARGIN:
        return LanguageGuess(None, f"ambiguous between {best} and {second}", scores, evidence)
    return LanguageGuess(best, f"clear signal for {best}", scores, evidence)
