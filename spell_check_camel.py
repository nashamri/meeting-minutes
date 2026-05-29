"""Arabic spell-check: CAMeL Tools for validity, Hunspell for suggestions.

A word is flagged when CAMeL's morphological analyzer can't produce any
analysis for it. That's a much better validity check than Hunspell alone
because CAMeL handles inflection natively (prefix and suffix clitics,
conjugations, etc.) — inflected forms that Hunspell over-flags pass
cleanly through CAMeL.

For suggestions we use Hunspell (via spylls) reading `assets/dictionaries/
ar.{aff,dic}`. The Aya `.aff` file ships REP (replacement), PHONE
(phonetic) and KEY (keyboard) rules tuned for Arabic typos, so Hunspell's
suggest() produces dramatically better candidates than blind edit-distance
generation. Hunspell's suggestions are then filtered back through CAMeL,
so anything CAMeL still rejects gets dropped.

Setup once:
    camel_data -i morphology-db-msa-r13
    # ar.aff/ar.dic live in assets/dictionaries/ (already in the repo)
"""

from __future__ import annotations

import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _bundled_camel_data_dir() -> Path:
    """Where the bundled morphology DB lives.

    In a PyInstaller bundle, assets unpack to _MEIPASS; in dev they sit
    next to the source. CAMeL Tools reads its data location from the
    CAMELTOOLS_DATA env var (see camel_tools/data/catalogue.py).
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "assets" / "camel_tools_data"


# Set CAMELTOOLS_DATA BEFORE any camel_tools import so the catalogue picks
# up our bundled path on first read. The env var only matters at the time
# camel_tools.data.catalogue is imported — we do this at module load
# instead of inside _get_analyzer().
_BUNDLED_DATA = _bundled_camel_data_dir()
if _BUNDLED_DATA.is_dir() and "CAMELTOOLS_DATA" not in os.environ:
    os.environ["CAMELTOOLS_DATA"] = str(_BUNDLED_DATA)

# Match runs of Arabic-block characters. The block also contains
# punctuation (،؛؟), digits (٠-٩), and signs, so we strip those off the
# boundaries afterwards via _trim_to_word_chars. Stripping at boundaries
# only — internal punctuation will still be matched as part of a "word",
# but in practice meeting text doesn't put commas in the middle of words.
_ARABIC_WORD_RE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿ]+")


def _trim_to_word_chars(s: str) -> tuple[int, int]:
    """Return (start, end) offsets so s[start:end] is just letters + marks.

    Uses Unicode general category — keeps L* (letters) and M* (combining
    marks / diacritics so tashkeel survives), drops everything else
    (punctuation, digits, signs, separators). Returns (0, 0) if there
    are no letter chars at all.
    """
    n = len(s)
    start = 0
    while start < n and unicodedata.category(s[start])[0] not in ("L", "M"):
        start += 1
    end = n
    while end > start and unicodedata.category(s[end - 1])[0] not in ("L", "M"):
        end -= 1
    return start, end

_RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
_HUNSPELL_PREFIX = _RESOURCE_ROOT / "assets" / "dictionaries" / "ar"

_analyzer = None  # CAMeL analyzer — lazy-initialised


def _get_analyzer():
    """Load the Calima MSA analyzer the first time it's needed.

    The load takes ~1.5 s. Calling code should already be running off the
    asyncio loop (e.g. via asyncio.to_thread) so the first invocation
    doesn't block the UI.
    """
    global _analyzer
    if _analyzer is None:
        from camel_tools.morphology.analyzer import Analyzer
        from camel_tools.morphology.database import MorphologyDB

        db = MorphologyDB.builtin_db("calima-msa-r13", "a")
        _analyzer = Analyzer(db)
    return _analyzer


@lru_cache(maxsize=1)
def _get_hunspell():
    """Load the Arabic Hunspell dictionary used for suggestion generation.

    Returns None if the files aren't present so the UI can fall back to
    edit-distance candidates rather than failing the whole check.
    """
    aff = _HUNSPELL_PREFIX.with_suffix(".aff")
    dic = _HUNSPELL_PREFIX.with_suffix(".dic")
    if not (aff.is_file() and dic.is_file()):
        return None
    try:
        from spylls.hunspell import Dictionary

        return Dictionary.from_files(str(_HUNSPELL_PREFIX))
    except Exception:
        return None


def is_available() -> bool:
    """Cheap pre-check: can we load the analyzer at all?

    Returns False if the Calima DB hasn't been downloaded yet, so callers
    can show a helpful "run camel_data -i ..." message instead of a stack
    trace.
    """
    try:
        _get_analyzer()
        return True
    except Exception:
        return False


@dataclass
class SpellIssue:
    word: str
    start: int  # byte offset into the original text
    end: int


def check_text(text: str, ignored: set[str] | None = None) -> list[SpellIssue]:
    """Return Arabic words in `text` the analyzer rejects.

    Non-Arabic words are skipped silently. Words in `ignored` are skipped.
    A word is flagged at most once per call even if it appears multiple
    times — the dialog will show one entry; resolving it edits all
    occurrences via offset-shift bookkeeping in the UI layer.
    """
    if not text:
        return []
    try:
        analyzer = _get_analyzer()
    except Exception:
        return []
    ignored = ignored or set()
    issues: list[SpellIssue] = []
    seen_valid: set[str] = set()
    for m in _ARABIC_WORD_RE.finditer(text):
        raw = m.group()
        s, e = _trim_to_word_chars(raw)
        if s == e:
            continue
        word = raw[s:e]
        if word in ignored or word in seen_valid:
            continue
        if analyzer.analyze(word):
            seen_valid.add(word)
            continue
        issues.append(
            SpellIssue(
                word=word,
                start=m.start() + s,
                end=m.start() + e,
            )
        )
    return issues


# Arabic letters used for the edit-distance fallback when Hunspell isn't
# available. Same set as before, kept around so the module stays useful
# even without the .aff/.dic files.
_ARABIC_LETTERS = "ابتثجحخدذرزسشصضطظعغفقكلمنهويأإآةىءؤئ"


def _edit_distance_candidates(word: str) -> list[str]:
    n = len(word)
    candidates: list[str] = []
    seen: set[str] = {word, ""}

    def _add(c: str) -> None:
        if c not in seen:
            seen.add(c)
            candidates.append(c)

    for i in range(n):
        _add(word[:i] + word[i + 1 :])  # deletion
        for letter in _ARABIC_LETTERS:
            _add(word[:i] + letter + word[i + 1 :])  # substitution
        if i + 1 < n:  # transposition
            _add(word[:i] + word[i + 1] + word[i] + word[i + 2 :])
    for i in range(n + 1):
        for letter in _ARABIC_LETTERS:
            _add(word[:i] + letter + word[i:])  # insertion
    return candidates


def get_suggestions(word: str, max_suggestions: int = 5) -> list[str]:
    """Hunspell-generated suggestions, filtered through CAMeL.

    Hunspell's suggest() uses .aff-defined PHONE/REP/KEY rules tuned for
    the language — it produces phonetically and orthographically-likely
    candidates rather than blind edit-distance neighbours. We then pass
    each suggestion through CAMeL's analyzer and drop anything CAMeL
    rejects. Falls back to a CAMeL-filtered edit-distance generator if
    Hunspell isn't available.
    """
    try:
        analyzer = _get_analyzer()
    except Exception:
        return []

    hunspell = _get_hunspell()
    # Hunspell's suggest() is super-linear in word length for Arabic
    # (rich morphology + dense .aff rules). At ~12 chars it can take 30s+
    # to yield a single candidate, so skip it for long words and use the
    # edit-distance fallback instead.
    HUNSPELL_MAX_LEN = 10
    if hunspell is not None and len(word) <= HUNSPELL_MAX_LEN:
        accepted: list[str] = []
        # Both an iteration cap and a wall-clock budget so we bail out
        # whichever comes first. A single yield can still block beyond
        # the budget, so the cap is a backstop.
        MAX_CANDIDATES = 60
        TIME_BUDGET_S = 1.5
        deadline = time.time() + TIME_BUDGET_S
        try:
            for i, suggestion in enumerate(hunspell.suggest(word)):
                if i >= MAX_CANDIDATES or time.time() > deadline:
                    break
                if suggestion == word:
                    continue
                if analyzer.analyze(suggestion):
                    accepted.append(suggestion)
                    if len(accepted) >= max_suggestions:
                        return accepted
        except Exception:
            pass
        if accepted:
            return accepted

    # Fallback: edit-distance candidates filtered through CAMeL.
    accepted_fallback: list[str] = []
    for cand in _edit_distance_candidates(word):
        if analyzer.analyze(cand):
            accepted_fallback.append(cand)
            if len(accepted_fallback) >= max_suggestions:
                break
    return accepted_fallback
