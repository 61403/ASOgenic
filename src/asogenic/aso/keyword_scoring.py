"""Turns free ASO signals into 0-100 scores. Port of
reference/swift/Services/ASO/KeywordScoring.swift, verbatim — the weightings are load-bearing
and must not drift.

Swift's `.rounded()` is round-half-away-from-zero (not Python's round-half-to-even), and every
value here lives in [0, 100], so `_round_half_up` replicates it via floor(x + 0.5). Swift's plain
`Int(x)` (no `.rounded()`) truncates toward zero; for our non-negative domain that's `int(x)`.
"""

from __future__ import annotations

import math

BRAND_POPULARITY_CEILING = 60


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 100.0)


def _round_half_up(value: float) -> int:
    return math.floor(value + 0.5)


def popularity(
    *,
    hint_position: int | None,
    prefix_ratio: float | None,
    competitor_frequency: int = 0,
    competitor_sample_size: int = 0,
) -> int:
    competitor_score = (
        min(competitor_frequency / competitor_sample_size, 1) * 100 if competitor_sample_size > 0 else 0.0
    )

    if hint_position is None:
        # No autocomplete evidence: competitor usage alone is weaker evidence than "people type
        # this", capped well below a corroborated term.
        return _round_half_up(competitor_score * 0.5)

    position_score = max(0.0, 100 - hint_position * 8)

    if prefix_ratio is None:
        return int(_clamp(position_score * 0.75 + competitor_score * 0.25))

    prefix_score = _clamp((1 - prefix_ratio) * 100)
    return _round_half_up(_clamp(position_score * 0.55 + prefix_score * 0.25 + competitor_score * 0.20))


def brand_popularity(*, appearances: int, search_count: int, user_rating_count: int | None) -> int:
    if search_count <= 0 or appearances <= 0:
        return 0
    reach = min(appearances / search_count, 1) * 100
    ratings = user_rating_count or 0
    fame = _clamp(math.log10(ratings) / 6 * 100) if ratings > 0 else 0.0
    return _round_half_up(min(_clamp(reach * 0.5 + fame * 0.5), float(BRAND_POPULARITY_CEILING)))


def competition(*, top_rating_counts: list[int]) -> int:
    counts = [c for c in top_rating_counts if c > 0]
    if not counts:
        return 0
    mean_log = sum(math.log10(c) for c in counts) / len(counts)
    # log10(1_000_000) = 6 is treated as a fully saturated market.
    return _round_half_up(_clamp(mean_log / 6 * 100))


def relevance(*, result_genre_ids: list[int | None], target_genre_id: int | None) -> int:
    if target_genre_id is None:
        return 100
    known = [g for g in result_genre_ids if g is not None]
    if not known:
        return 100
    matches = sum(1 for g in known if g == target_genre_id)
    return _round_half_up(matches / len(known) * 100)


def competition_multiplier(competition_score: int) -> float:
    """1 - 0.3 * (clamp(c, 0, 100) / 100) — a capped 30% haircut. See data-model.md#KeywordTerm."""
    return 1 - 0.3 * (_clamp(float(competition_score)) / 100)
