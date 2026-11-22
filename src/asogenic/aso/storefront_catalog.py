"""Locale -> storefront research target. Port of
reference/swift/Services/ASO/StorefrontCatalog.swift, verbatim — every row (country, style,
grounding) is a probed, live-verified fact, not a guess. See data-model.md #StorefrontMapping.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from asogenic.aso.storefront_ids import storefront_id_for_country
from asogenic.models.locales import canonical_locale_id


class GroundingExpectation(enum.Enum):
    FULL = "full"
    WEAK = "weak"
    NONE = "none"


class KeywordFieldStyle(enum.Enum):
    LATIN_SINGLE_WORDS = "latinSingleWords"
    CJK_PHRASES = "cjkPhrases"
    RTL_SINGLE_WORDS = "rtlSingleWords"
    INFLECTED_SINGLE_WORDS = "inflectedSingleWords"
    COMPOUND_AWARE = "compoundAware"

    @property
    def allows_multi_word_terms(self) -> bool:
        return self is KeywordFieldStyle.CJK_PHRASES

    @property
    def max_term_characters(self) -> int:
        return 6 if self is KeywordFieldStyle.CJK_PHRASES else 25


@dataclass(frozen=True)
class StorefrontMapping:
    country_code: str
    storefront_id: int
    grounding: GroundingExpectation
    keyword_style: KeywordFieldStyle


@dataclass(frozen=True)
class _CatalogEntry:
    country: str
    style: KeywordFieldStyle
    grounding: GroundingExpectation


_L = KeywordFieldStyle.LATIN_SINGLE_WORDS
_C = KeywordFieldStyle.CJK_PHRASES
_R = KeywordFieldStyle.RTL_SINGLE_WORDS
_I = KeywordFieldStyle.INFLECTED_SINGLE_WORDS
_D = KeywordFieldStyle.COMPOUND_AWARE
_FULL = GroundingExpectation.FULL
_WEAK = GroundingExpectation.WEAK
_NONE = GroundingExpectation.NONE

_COUNTRY_BY_LOCALE: dict[str, _CatalogEntry] = {
    "ar-SA": _CatalogEntry("SA", _R, _FULL),
    "bn-BD": _CatalogEntry("BD", _L, _NONE),
    "ca": _CatalogEntry("ES", _L, _WEAK),
    "cs": _CatalogEntry("CZ", _I, _FULL),
    "da": _CatalogEntry("DK", _L, _FULL),
    "de-DE": _CatalogEntry("DE", _D, _FULL),
    "el": _CatalogEntry("GR", _I, _FULL),
    "en-AU": _CatalogEntry("AU", _L, _FULL),
    "en-CA": _CatalogEntry("CA", _L, _FULL),
    "en-GB": _CatalogEntry("GB", _L, _FULL),
    "en-US": _CatalogEntry("US", _L, _FULL),
    "es-ES": _CatalogEntry("ES", _L, _FULL),
    "es-MX": _CatalogEntry("MX", _L, _FULL),
    "fi": _CatalogEntry("FI", _I, _FULL),
    "fr-CA": _CatalogEntry("CA", _L, _FULL),
    "fr-FR": _CatalogEntry("FR", _L, _FULL),
    "gu-IN": _CatalogEntry("IN", _L, _NONE),
    "he": _CatalogEntry("IL", _R, _FULL),
    "hi": _CatalogEntry("IN", _L, _NONE),
    "hr": _CatalogEntry("HR", _I, _FULL),
    "hu": _CatalogEntry("HU", _I, _FULL),
    "id": _CatalogEntry("ID", _L, _FULL),
    "it": _CatalogEntry("IT", _L, _FULL),
    "ja": _CatalogEntry("JP", _C, _FULL),
    "kn-IN": _CatalogEntry("IN", _L, _NONE),
    "ko": _CatalogEntry("KR", _C, _FULL),
    "ml-IN": _CatalogEntry("IN", _L, _NONE),
    "mr-IN": _CatalogEntry("IN", _L, _NONE),
    "ms": _CatalogEntry("MY", _L, _WEAK),
    "nl-NL": _CatalogEntry("NL", _L, _FULL),
    "no": _CatalogEntry("NO", _L, _FULL),
    "or-IN": _CatalogEntry("IN", _L, _NONE),
    "pa-IN": _CatalogEntry("IN", _L, _NONE),
    "pl": _CatalogEntry("PL", _I, _FULL),
    "pt-BR": _CatalogEntry("BR", _L, _FULL),
    "pt-PT": _CatalogEntry("PT", _L, _FULL),
    "ro": _CatalogEntry("RO", _I, _FULL),
    "ru": _CatalogEntry("RU", _I, _FULL),
    "sk": _CatalogEntry("SK", _I, _FULL),
    "sl-SI": _CatalogEntry("SI", _I, _FULL),
    "sv": _CatalogEntry("SE", _L, _FULL),
    "ta-IN": _CatalogEntry("IN", _L, _NONE),
    "te-IN": _CatalogEntry("IN", _L, _NONE),
    "th": _CatalogEntry("TH", _C, _FULL),
    "tr": _CatalogEntry("TR", _I, _FULL),
    "uk": _CatalogEntry("UA", _I, _FULL),
    "ur-PK": _CatalogEntry("PK", _R, _NONE),
    "vi": _CatalogEntry("VN", _L, _FULL),
    "zh-Hans": _CatalogEntry("CN", _C, _FULL),
    "zh-Hant": _CatalogEntry("TW", _C, _FULL),
}


def mapping_for_locale(locale_id: str) -> StorefrontMapping | None:
    """The research target for a locale, or None when there is nothing to research against —
    the caller falls back to an ungrounded, clearly-badged AI suggestion."""
    entry = _COUNTRY_BY_LOCALE.get(canonical_locale_id(locale_id))
    if entry is None or entry.grounding is GroundingExpectation.NONE:
        return None
    storefront_id = storefront_id_for_country(entry.country)
    if storefront_id is None:
        return None
    return StorefrontMapping(
        country_code=entry.country,
        storefront_id=storefront_id,
        grounding=entry.grounding,
        keyword_style=entry.style,
    )


def keyword_style_for_locale(locale_id: str) -> KeywordFieldStyle:
    entry = _COUNTRY_BY_LOCALE.get(canonical_locale_id(locale_id))
    return entry.style if entry is not None else KeywordFieldStyle.LATIN_SINGLE_WORDS


def keyword_style_is_explicit(locale_id: str) -> bool:
    return canonical_locale_id(locale_id) in _COUNTRY_BY_LOCALE


def grounding_for_locale(locale_id: str) -> GroundingExpectation:
    entry = _COUNTRY_BY_LOCALE.get(canonical_locale_id(locale_id))
    if entry is None:
        return GroundingExpectation.NONE
    if storefront_id_for_country(entry.country) is None:
        return GroundingExpectation.NONE
    return entry.grounding
