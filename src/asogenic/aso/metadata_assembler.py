"""Builds the final keyword field from a researched KeywordPool, deterministically. Port of
reference/swift/Services/ASO/MetadataAssembler.swift — THE "must not drift" logic (README.md).

The model never picks keywords. Research decides which terms are real; this decides which of
them fit. Every inclusion and omission has a written reason (KeywordDropReason).
"""

from __future__ import annotations

from dataclasses import dataclass

from asogenic.aso.listing_tokenizer import (
    candidate_terms,
    contains_right_to_left_letters,
    indexed_tokens,
    is_platform_term,
)
from asogenic.aso.listing_tokenizer import normalise as _normalise
from asogenic.aso.storefront_catalog import KeywordFieldStyle
from asogenic.models.keywords import (
    CoverageReport,
    DroppedTerm,
    IdentityAlternative,
    KeywordDropReason,
    KeywordPool,
    KeywordSource,
    KeywordTerm,
)

KEYWORD_BYTE_LIMIT = 100
MINIMUM_RELEVANCE = 25
MINIMUM_IDENTITY_RELEVANCE = 40
NAME_CHARACTER_LIMIT = 30
SUBTITLE_CHARACTER_LIMIT = 30
COVERAGE_DECAY = 0.6


def _byte_len(s: str) -> int:
    return len(s.encode("utf-8"))


# -- quality bars -----------------------------------------------------------------


def meets_quality_bar(term: KeywordTerm) -> bool:
    """Enough evidence, our category, not a platform word. Deliberately excludes shape rules
    (violates_style) — visible copy is checked separately by meets_identity_quality_bar."""
    if term.popularity <= 0:
        return False
    if term.relevance is None or term.relevance < MINIMUM_RELEVANCE:
        return False
    return not is_platform_term(term.term)


def meets_identity_quality_bar(term: KeywordTerm) -> bool:
    """Stricter bar for name/subtitle: same as meets_quality_bar but relevance >= 40."""
    if term.popularity <= 0:
        return False
    if term.relevance is None or term.relevance < MINIMUM_IDENTITY_RELEVANCE:
        return False
    return not is_platform_term(term.term)


def violates_style(
    term: str, *, style: KeywordFieldStyle, is_brand_term: bool = False, is_autocomplete_confirmed: bool = False
) -> bool:
    """Script-level rules that make a term unusable in this locale's keyword field. Shared with
    KeywordFieldEditor so a hand-added term and an assembled one can never disagree."""
    trimmed = term.strip()
    if not trimmed:
        return True
    if len(trimmed) > style.max_term_characters:
        return True
    if not is_brand_term and not style.allows_multi_word_terms and " " in trimmed:
        return True
    if len(trimmed) < 2:
        return True
    return (
        style is KeywordFieldStyle.RTL_SINGLE_WORDS
        and not is_brand_term
        and not is_autocomplete_confirmed
        and not contains_right_to_left_letters(trimmed)
    )


# -- covers / phrase weighting -----------------------------------------------------


def _covers(term: str, *, normalised_text: str, tokens: set[str], style: KeywordFieldStyle) -> bool:
    key = _normalise(term)
    if not key:
        return False
    if style.allows_multi_word_terms or " " in key:
        return key in normalised_text
    return key in tokens


def _phrase_weight(term: str, *, style: KeywordFieldStyle) -> float:
    if style.allows_multi_word_terms:
        return min(1 + max(len(term) - 2, 0) * 0.25, 2.0)
    words = len(term.split(" "))
    return min(1 + max(words - 1, 0) * 0.6, 2.2)


def _coverage_score(covered: list[KeywordTerm], *, style: KeywordFieldStyle) -> float:
    weighted = sorted(
        (term.field_priority * _phrase_weight(term.term, style=style) for term in covered), reverse=True
    )
    return sum(entry * (COVERAGE_DECAY**i) for i, entry in enumerate(weighted))


# -- ordering -----------------------------------------------------------------------


def _shows_before_key(term: KeywordTerm) -> tuple[int, str]:
    return (-term.popularity, _normalise(term.term))


def _fills_before_key(term: KeywordTerm) -> tuple[float, int, str]:
    return (-term.field_priority, _byte_len(term.term), _normalise(term.term))


# -- identity (name/subtitle) assembly -----------------------------------------------


def _usable_candidates(candidates: list[str], *, limit: int, fallback: str) -> list[str]:
    seen: set[str] = set()
    usable: list[str] = []
    for candidate in candidates:
        trimmed = candidate.strip()
        if not trimmed or len(trimmed) > limit:
            continue
        key = _normalise(trimmed)
        if key in seen:
            continue
        seen.add(key)
        usable.append(trimmed)

    trimmed_fallback = fallback.strip()
    if not usable and trimmed_fallback and len(trimmed_fallback) <= limit:
        usable = [trimmed_fallback]
    return usable


@dataclass(frozen=True)
class IdentityResult:
    name: str
    subtitle: str
    covered: list[KeywordTerm]
    alternatives: list[IdentityAlternative]


def assemble_identity(
    *,
    pool: KeywordPool,
    name_candidates: list[str],
    subtitle_candidates: list[str],
    current_name: str,
    current_subtitle: str,
) -> IdentityResult:
    style = pool.keyword_style
    scorable = [t for t in pool.terms if not t.is_brand_term and meets_identity_quality_bar(t)]

    names = _usable_candidates(name_candidates, limit=NAME_CHARACTER_LIMIT, fallback=current_name)
    subtitles = _usable_candidates(subtitle_candidates, limit=SUBTITLE_CHARACTER_LIMIT, fallback=current_subtitle)
    if not names:
        return IdentityResult(current_name, current_subtitle, [], [])

    subtitle_options = subtitles if subtitles else [""]

    scored: list[tuple[str, str, list[KeywordTerm], float]] = []
    for name in names:
        name_tokens = indexed_tokens(name, style=style)
        for subtitle in subtitle_options:
            subtitle_tokens = indexed_tokens(subtitle, style=style)
            overlap = len(name_tokens & subtitle_tokens)
            combined = _normalise(f"{name} {subtitle}")
            union_tokens = name_tokens | subtitle_tokens

            covered = [
                t for t in scorable if _covers(t.term, normalised_text=combined, tokens=union_tokens, style=style)
            ]
            score = _coverage_score(covered, style=style) - overlap * 25
            scored.append((name, subtitle, covered, score))

    def rank_key(entry: tuple[str, str, list[KeywordTerm], float]) -> tuple[float, int, str]:
        name, subtitle, _covered, score = entry
        return (-score, len(name) + len(subtitle), name + subtitle)

    ranked = sorted(scored, key=rank_key)
    if not ranked:
        return IdentityResult(current_name, current_subtitle, [], [])

    best = ranked[0]
    best_name, best_subtitle, best_covered, _best_score = best

    alternatives = [
        IdentityAlternative(
            name=name, subtitle=subtitle, covered_popularity=sum(t.popularity for t in covered)
        )
        for name, subtitle, covered, _score in ranked[1:]
        if name != best_name or subtitle != best_subtitle
    ][:4]

    return IdentityResult(
        name=best_name,
        subtitle=best_subtitle,
        covered=sorted(best_covered, key=_shows_before_key),
        alternatives=alternatives,
    )


def covered_terms(text: str, *, pool: KeywordPool) -> list[KeywordTerm]:
    """The researched terms a piece of visible copy actually covers, best-first. Same matching
    rule assemble_identity uses — must not drift, or the sheet claims coverage assembly wouldn't."""
    style = pool.keyword_style
    tokens = indexed_tokens(text, style=style)
    normalised = _normalise(text)
    matches = [
        t
        for t in pool.terms
        if not t.is_brand_term and t.popularity > 0 and (t.relevance or 0) >= MINIMUM_RELEVANCE
        and _covers(t.term, normalised_text=normalised, tokens=tokens, style=style)
    ]
    return sorted(matches, key=_shows_before_key)


# -- term shaping -----------------------------------------------------------------


def shape_terms(terms: list[KeywordTerm], *, style: KeywordFieldStyle) -> list[KeywordTerm]:
    """Decompose multi-word phrases into single words for styles that don't allow multi-word
    terms. Brand terms and already-single words are kept whole. Dedupe by normalised key,
    keeping the higher field_priority."""
    if style.allows_multi_word_terms:
        return terms

    best_by_key: dict[str, KeywordTerm] = {}
    order: list[str] = []

    def consider(candidate: KeywordTerm) -> None:
        key = _normalise(candidate.term)
        if not key:
            return
        existing = best_by_key.get(key)
        if existing is None:
            best_by_key[key] = candidate
            order.append(key)
        elif candidate.field_priority > existing.field_priority:
            best_by_key[key] = candidate

    for term in terms:
        trimmed = term.term.strip()
        if term.is_brand_term or " " not in trimmed:
            consider(term)
            continue
        for word in candidate_terms(trimmed, style=style):
            consider(
                KeywordTerm(
                    term=word,
                    sources=term.sources,
                    popularity=term.popularity,
                    competition=term.competition,
                    relevance=term.relevance,
                    our_rank=None,
                    is_brand_term=False,
                )
            )

    shaped = [best_by_key[key] for key in order]
    return sorted(shaped, key=_fills_before_key)


def filter_by_seed_overlap(terms: list[KeywordTerm], *, style: KeywordFieldStyle) -> list[KeywordTerm]:
    seed_terms = [t for t in terms if KeywordSource.SEED in t.sources]
    if not seed_terms:
        return terms

    seed_tokens: set[str] = set()
    for t in seed_terms:
        seed_tokens |= indexed_tokens(t.term, style=style)

    def keep(term: KeywordTerm) -> bool:
        if KeywordSource.SEED in term.sources:
            return True
        term_tokens = indexed_tokens(term.term, style=style)
        if style is KeywordFieldStyle.CJK_PHRASES:
            # CJK tokenization includes 2-character n-grams. Those are too common to
            # establish intent overlap (e.g. unrelated travel suggestions), so require a
            # corroborating 3+ character n-gram for harvested non-seed terms.
            return any(
                len(token) >= 3 and token in seed_tokens
                for token in term_tokens
            )
        return not seed_tokens.isdisjoint(term_tokens)

    return [t for t in terms if keep(t)]


# -- disqualification --------------------------------------------------------------


def _disqualify(
    term: KeywordTerm,
    *,
    key: str,
    style: KeywordFieldStyle,
    used_keys: set[str],
    indexed_elsewhere: set[str],
    category_tokens: set[str],
    brand_tokens: set[str],
    excluded_brand_keys: set[str],
) -> KeywordDropReason | None:
    # Brand terms are never publish-safe by default. The caller can only opt into them through a
    # future explicit policy parameter; exclusion lists remain useful for a precise diagnostic.
    if key in excluded_brand_keys:
        return KeywordDropReason.BRAND_TERM_EXCLUDED
    if term.is_brand_term:
        return KeywordDropReason.BRAND_TERM_EXCLUDED
    if key in used_keys:
        return KeywordDropReason.DUPLICATE_TERM
    if key in brand_tokens:
        return KeywordDropReason.OWN_BRAND
    if key in category_tokens:
        return KeywordDropReason.CATEGORY_NAME
    if is_platform_term(term.term):
        return KeywordDropReason.PLATFORM_TERM
    if key in indexed_elsewhere:
        return KeywordDropReason.DUPLICATE_OF_NAME_OR_SUBTITLE
    if violates_style(
        term.term, style=style, is_brand_term=term.is_brand_term,
        is_autocomplete_confirmed=KeywordSource.AUTOCOMPLETE in term.sources,
    ):
        return KeywordDropReason.STYLE_RULE
    if term.relevance is None:
        return KeywordDropReason.UNVERIFIED
    if term.relevance < MINIMUM_RELEVANCE:
        return KeywordDropReason.LOW_RELEVANCE
    if term.popularity <= 0:
        return KeywordDropReason.LOW_RELEVANCE
    return None


# -- keyword field assembly ---------------------------------------------------------


def assemble_keywords(
    *,
    pool: KeywordPool,
    name: str,
    subtitle: str,
    category_name: str | None = None,
    brand_name: str | None = None,
    excluded_brand_terms: frozenset[str] = frozenset(),
) -> tuple[str, CoverageReport]:
    style = pool.keyword_style
    indexed_elsewhere = indexed_tokens(f"{name} {subtitle}", style=style)
    category_tokens = indexed_tokens(category_name, style=style) if category_name else set()
    brand_tokens = indexed_tokens(brand_name, style=style) if brand_name else set()
    excluded_brand_keys = {_normalise(t) for t in excluded_brand_terms if _normalise(t)}
    excluded_brand_tokens = {
        _normalise(token)
        for value in excluded_brand_terms
        for token in value.replace(",", " ").split()
        if _normalise(token)
    }

    covered: list[KeywordTerm] = []
    dropped: list[DroppedTerm] = []
    used_keys: set[str] = set()
    bytes_used = 0

    # Keep the source-listing field anchored to the caller's researched intent before Latin
    # phrase shaping splits multi-word candidates into individual tokens. Without this gate,
    # competitor-only harvests such as "mobile" or "airport" can outrank the supplied seed
    # phrases and become publishable keywords despite having no seed-token overlap.
    shaped = shape_terms(filter_by_seed_overlap(pool.terms, style=style), style=style)
    if not any(KeywordSource.SEED in term.sources for term in shaped):
        # A pool with no seed evidence must never publish competitor-only harvests, but
        # retain direct autocomplete terms for backward-compatible diagnostics and reports.
        shaped = [term for term in shaped if KeywordSource.COMPETITOR not in term.sources]
    if style is KeywordFieldStyle.CJK_PHRASES:
        # CJK candidate generation intentionally creates n-grams, but packing every
        # overlapping fragment produces a byte-valid field with unusable repetition.
        # Preserve explicit seeds and prefer the longest researched surface instead.
        shaped = [
            term for term in shaped
            if KeywordSource.SEED in term.sources
            or not any(
                _normalise(term.term) != _normalise(other.term)
                and _normalise(term.term) in _normalise(other.term)
                and len(_normalise(other.term)) > len(_normalise(term.term))
                for other in shaped
            )
        ]

    for term in shaped:
        key = _normalise(term.term)

        if key in excluded_brand_tokens:
            dropped.append(DroppedTerm(term=term, reason=KeywordDropReason.BRAND_TERM_EXCLUDED))
            continue

        reason = _disqualify(
            term,
            key=key,
            style=style,
            used_keys=used_keys,
            indexed_elsewhere=indexed_elsewhere,
            category_tokens=category_tokens,
            brand_tokens=brand_tokens,
            excluded_brand_keys=excluded_brand_keys,
        )
        if reason is not None:
            dropped.append(DroppedTerm(term=term, reason=reason))
            continue

        cost = (0 if not covered else 1) + _byte_len(term.term)
        if bytes_used + cost > KEYWORD_BYTE_LIMIT:
            dropped.append(DroppedTerm(term=term, reason=KeywordDropReason.BYTE_BUDGET))
            continue

        covered.append(term)
        used_keys.add(key)
        bytes_used += cost

    report = CoverageReport(
        locale_id=pool.locale_id,
        covered=covered,
        dropped=dropped,
        bytes_used=bytes_used,
        grounding=pool.grounding,
    )
    return ",".join(t.term for t in covered), report


# -- cross-locale overflow (the US keyword multiplier) ------------------------------


@dataclass(frozen=True)
class DistributionTarget:
    locale_id: str
    existing: str = ""


def distribute_source_terms(
    *,
    pool: KeywordPool,
    targets: list[DistributionTarget],
    already_covered: set[str],
    excluded_brand_terms: frozenset[str] = frozenset(),
) -> dict[str, tuple[str, CoverageReport]]:
    if not targets:
        return {}
    style = pool.keyword_style
    excluded_brand_keys = {_normalise(t) for t in excluded_brand_terms}

    def eligible(term: KeywordTerm) -> bool:
        key = _normalise(term.term)
        if key in already_covered:
            return False
        if term.is_brand_term and key in excluded_brand_keys:
            return False
        if not meets_quality_bar(term):
            return False
        return not violates_style(
            term.term, style=style, is_brand_term=term.is_brand_term,
            is_autocomplete_confirmed=KeywordSource.AUTOCOMPLETE in term.sources,
        )

    remaining = [t for t in shape_terms(filter_by_seed_overlap(pool.terms, style=style), style=style) if eligible(t)]

    results: dict[str, tuple[str, CoverageReport]] = {}
    used_keys: set[str] = set()
    for target in targets:
        for existing in target.existing.split(","):
            key = _normalise(existing)
            if key:
                used_keys.add(key)

    for target in targets:
        existing_terms = [t for t in target.existing.split(",") if t.strip()]
        bytes_used = 0 if not target.existing else _byte_len(target.existing)
        added: list[KeywordTerm] = []

        for term in remaining:
            key = _normalise(term.term)
            if key in used_keys:
                continue
            cost = (0 if bytes_used == 0 else 1) + _byte_len(term.term)
            if bytes_used + cost > KEYWORD_BYTE_LIMIT:
                continue
            added.append(
                KeywordTerm(
                    term=term.term,
                    sources={KeywordSource.SOURCE_LOCALE},
                    popularity=term.popularity,
                    competition=term.competition,
                    relevance=term.relevance,
                    our_rank=None,
                    is_brand_term=term.is_brand_term,
                )
            )
            used_keys.add(key)
            bytes_used += cost

        if not added:
            continue
        added_terms = {a.term for a in added}
        remaining = [t for t in remaining if t.term not in added_terms]

        field_value = ",".join(existing_terms + [a.term for a in added])
        results[target.locale_id] = (
            field_value,
            CoverageReport(
                locale_id=target.locale_id,
                covered=added,
                dropped=[DroppedTerm(term=t, reason=KeywordDropReason.BYTE_BUDGET) for t in remaining],
                bytes_used=_byte_len(field_value),
                grounding=pool.grounding,
            ),
        )
    return results
