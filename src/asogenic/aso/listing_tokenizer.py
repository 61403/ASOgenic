"""Comparison/tokenization layer shared by MetadataAssembler and the research service — they must
tokenize identically, or a term the name already covers can slip into the keyword field. Port of
reference/swift/Services/ASO/ListingTokenizer.swift."""

from __future__ import annotations

import re
import unicodedata

from asogenic.aso.storefront_catalog import KeywordFieldStyle

STOPWORDS: frozenset[str] = frozenset(
    {
        # English
        "a", "an", "the", "and", "or", "for", "of", "in", "on", "at", "to", "with", "your",
        "you", "is", "are", "this", "that", "it", "by", "from", "as", "be", "my",
        # Romance/Germanic function words
        "el", "la", "los", "las", "de", "del", "y", "para", "con", "un", "una",
        "le", "les", "du", "des", "et", "pour", "avec", "sur",
        "der", "die", "das", "und", "für", "mit", "den", "dem",
        "o", "os", "do", "da", "dos", "em", "com",
        "il", "lo", "gli", "e", "per",
    }
)

PLATFORM_TERMS: frozenset[str] = frozenset(
    {
        "app", "apps", "apple", "ios", "iphone", "ipad", "ipados", "watchos", "macos", "android",
        "appstore", "app store",
    }
)

MAX_BRAND_WORDS = 3

_RTL_RANGES: tuple[tuple[int, int], ...] = (
    (0x0590, 0x05FF),  # Hebrew
    (0x0600, 0x06FF),  # Arabic
    (0x0750, 0x077F),  # Arabic Supplement
    (0x08A0, 0x08FF),  # Arabic Extended-A
    (0xFB1D, 0xFB4F),  # Hebrew presentation forms
    (0xFB50, 0xFDFF),  # Arabic presentation forms A
    (0xFE70, 0xFEFF),  # Arabic presentation forms B
)

_MARKS_RE = re.compile("[®™©℠]")
_SEPARATOR_CHARS = frozenset("-–—:|")  # -, en dash, em dash, colon, pipe


def contains_right_to_left_letters(term: str) -> bool:
    return any(any(lo <= ord(ch) <= hi for lo, hi in _RTL_RANGES) for ch in term)


def normalise(value: str) -> str:
    """Case-, diacritic-, and width-insensitive comparison key. Never write this form — the
    surface form is what ranks in inflected languages."""
    value = value.strip()
    value = unicodedata.normalize("NFKC", value)  # width folding (fullwidth/halfwidth)
    decomposed = unicodedata.normalize("NFKD", value)
    without_diacritics = "".join(
        c for c in decomposed
        if not unicodedata.combining(c) and unicodedata.category(c) not in {"Cf", "Cc"}
    )
    return without_diacritics.casefold()


def is_platform_term(term: str) -> bool:
    return normalise(term) in PLATFORM_TERMS


def _has_letter(s: str) -> bool:
    return any(c.isalpha() for c in s)


def _words(text: str) -> list[str]:
    """Splits on whitespace after replacing punctuation/symbol characters with spaces."""
    chars = [" " if unicodedata.category(ch)[0] in ("P", "S") else ch for ch in text]
    return "".join(chars).split()


def indexed_tokens(text: str, *, style: KeywordFieldStyle) -> set[str]:
    words = _words(text)
    result = {normalise(w) for w in words}

    if not style.allows_multi_word_terms:
        return result

    for word in words:
        characters = list(word)
        if len(characters) <= 1:
            continue
        for length in range(2, min(4, len(characters)) + 1):
            for start in range(len(characters) - length + 1):
                result.add(normalise("".join(characters[start : start + length])))
    return result


def candidate_terms(text: str, *, style: KeywordFieldStyle) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    def append(candidate: str) -> None:
        trimmed = candidate.strip()
        if not (2 <= len(trimmed) <= style.max_term_characters):
            return
        if not _has_letter(trimmed):
            return
        key = normalise(trimmed)
        if key in STOPWORDS or key in seen:
            return
        seen.add(key)
        result.append(trimmed)

    for word in _words(text):
        if style.allows_multi_word_terms:
            characters = list(word)
            if len(characters) <= 1:
                continue
            for length in range(2, min(4, len(characters)) + 1):
                for start in range(len(characters) - length + 1):
                    append("".join(characters[start : start + length]))
        else:
            append(word)
    return result


def _split_on_separator_chars(text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    for ch in text:
        if ch in _SEPARATOR_CHARS:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _strip_non_alnum_edges(s: str) -> str:
    start, end = 0, len(s)
    while start < end and not s[start].isalnum():
        start += 1
    while end > start and not s[end - 1].isalnum():
        end -= 1
    return s[start:end]


def _undecorated(brand: str) -> str | None:
    without_marks = _MARKS_RE.sub("", brand)
    trimmed = _strip_non_alnum_edges(without_marks.strip())
    if len(trimmed) < 2 or not _has_letter(trimmed):
        return None
    return trimmed


def brand_segment(track_name: str) -> str | None:
    leading = _split_on_separator_chars(track_name)[0].strip()
    if len(leading) < 2 or not _has_letter(leading):
        return None

    words = [w for w in leading.split(" ") if w != ""]
    if len(words) > 1 and normalise(words[0]) in STOPWORDS:
        words = words[1:]

    significant = [w for w in words if _has_letter(w)]
    if len(significant) <= MAX_BRAND_WORDS:
        return _undecorated(" ".join(words))

    kept: list[str] = []
    letter_words = 0
    for word in words:
        kept.append(word)
        if _has_letter(word):
            letter_words += 1
        if letter_words == 2:
            break
    return _undecorated(" ".join(kept))
