"""Turns guessed seed terms into a pool of terms verified to be searched in one App Store
storefront. Port of reference/swift/Services/ASO/ASOResearchService.swift — the other "must not
drift" file (README.md).

Every network call is bounded; every failure is swallowed per-seed/per-term. Research is
best-effort enrichment and must never fail a generation run — a locale with nothing to work with
degrades to an ungrounded pool.
"""

from __future__ import annotations

import asyncio
import contextvars
import enum
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Protocol

from asogenic.aso.keyword_scoring import brand_popularity, competition, popularity, relevance
from asogenic.aso.listing_tokenizer import (
    brand_segment,
    candidate_terms,
    contains_right_to_left_letters,
    indexed_tokens,
)
from asogenic.aso.listing_tokenizer import normalise as _normalise
from asogenic.aso.network_errors import HTTPStatusError, RateLimitedError
from asogenic.aso.storefront_catalog import (
    GroundingExpectation,
    KeywordFieldStyle,
    keyword_style_for_locale,
    mapping_for_locale,
)
from asogenic.aso.top_app_ranking import rank
from asogenic.models.keywords import KeywordPool, KeywordSource, KeywordTerm
from asogenic.models.storefront_listing import StorefrontAppListing

MAX_SEEDS = 8
MAX_EXPANDED_TERMS = 60
MAX_LADDER_TERMS = 3
MAX_LADDER_PROBES = 2
MAX_QUALIFIED_TERMS = 12
SEARCH_DEPTH = 50
MINIMUM_COMPETITOR_APPS = 2
MAX_CONCURRENCY = 4
MAX_HINT_REQUESTS_PER_LOCALE = MAX_SEEDS + MAX_LADDER_TERMS * MAX_LADDER_PROBES
STARTING_HINTS_REQUESTS_PER_MINUTE = 20


def estimated_research_minutes(locale_count: int) -> int:
    """Conservative (uses the throttler's starting rate, not its ramped ceiling) wall-clock
    estimate, rounded up, so the number is a ceiling a run beats rather than overruns."""
    if locale_count <= 0:
        return 0
    calls = locale_count * MAX_HINT_REQUESTS_PER_LOCALE
    minutes = -(-calls // STARTING_HINTS_REQUESTS_PER_MINUTE)  # ceil division
    return max(1, minutes)


class ResearchStage(enum.Enum):
    EXPANDING = "expanding"
    SCORING = "scoring"
    HARVESTING = "harvesting"
    FINISHED = "finished"


@dataclass(frozen=True)
class ResearchProgress:
    locale_id: str
    stage: ResearchStage
    completed: int
    total: int


@dataclass(frozen=True)
class ResearchDiagnostics:
    """Immutable request totals for one research run."""

    stage_counts: Mapping[str, Mapping[str, int]]
    failure_counts: Mapping[str, int]


@dataclass
class _DiagnosticsCollector:
    stage_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    failure_counts: dict[str, int] = field(default_factory=dict)

    def record(self, stage: str, error: Exception | None = None) -> None:
        counts = self.stage_counts.setdefault(stage, {"requests": 0, "succeeded": 0, "failed": 0})
        counts["requests"] += 1
        if error is None:
            counts["succeeded"] += 1
            return
        counts["failed"] += 1
        category = _failure_category(error)
        self.failure_counts[category] = self.failure_counts.get(category, 0) + 1

    def snapshot(self) -> ResearchDiagnostics:
        return ResearchDiagnostics(
            MappingProxyType({stage: MappingProxyType(dict(counts)) for stage, counts in self.stage_counts.items()}),
            MappingProxyType(dict(self.failure_counts)),
        )


def _failure_category(error: Exception) -> str:
    if isinstance(error, RateLimitedError):
        return "rate_limited"
    if isinstance(error, HTTPStatusError) and error.status in (403, 429):
        return f"http_{error.status}"
    return "other"


_current_diagnostics: contextvars.ContextVar[tuple[object, _DiagnosticsCollector] | None] = contextvars.ContextVar(
    "asogenic_research_diagnostics", default=None
)


def _record_diagnostic(stage: str, error: Exception | None = None) -> None:
    current = _current_diagnostics.get()
    if current is not None:
        current[1].record(stage, error)


ProgressCallback = Callable[[ResearchProgress], None]


class KeywordSuggesting(Protocol):
    async def suggestions(self, *, term: str, country: str) -> list[str]: ...


class StorefrontSearching(Protocol):
    async def search(self, *, term: str, country: str, limit: int) -> list[StorefrontAppListing]: ...

    async def lookup(self, *, bundle_id: str, country: str) -> StorefrontAppListing | None: ...


def _chunked(items: list[str], size: int) -> list[list[str]]:
    if size <= 0:
        return [items]
    return [items[i : i + size] for i in range(0, len(items), size)]


def _ungrounded_pool(locale_id: str, researched_at: datetime) -> KeywordPool:
    return KeywordPool(
        locale_id=locale_id,
        country_code="",
        terms=[],
        grounding=GroundingExpectation.NONE,
        keyword_style=keyword_style_for_locale(locale_id),
        category_name=None,
        top_apps=[],
        researched_at=researched_at,
    )


def _brand_key(track_name: str) -> str | None:
    brand = brand_segment(track_name)
    return _normalise(brand) if brand else None


def _contains_cjk(value: str) -> bool:
    return any(
        "CJK" in unicodedata.name(char, "")
        or "HIRAGANA" in unicodedata.name(char, "")
        or "KATAKANA" in unicodedata.name(char, "")
        or "THAI" in unicodedata.name(char, "")
        for char in value
    )


def _locale_term_allowed(term: str, sources: set[KeywordSource], style: KeywordFieldStyle) -> bool:
    if style is KeywordFieldStyle.RTL_SINGLE_WORDS:
        return contains_right_to_left_letters(term)
    if style is KeywordFieldStyle.CJK_PHRASES:
        if not _contains_cjk(term):
            return False
        # Two-character n-grams harvested from app names are usually fragments (e.g. アプ,
        # 元数). Keep short terms only when Apple autocomplete or the user's seed corroborates
        # them directly.
        return len(term) >= 3 or bool(sources & {KeywordSource.AUTOCOMPLETE, KeywordSource.SEED})
    return True


@dataclass(frozen=True)
class _HarvestedToken:
    term: str
    track_id: int
    is_on_category: bool


@dataclass(frozen=True)
class _TermOutcome:
    relevance: int
    competition: int
    our_rank: int | None
    harvested: list[_HarvestedToken]
    brand_keys: set[str]
    category_names: list[str]
    sample_size: int
    listings: list[StorefrontAppListing]


class ASOResearchService:
    def __init__(
        self,
        *,
        hints_client: KeywordSuggesting,
        search_client: StorefrontSearching,
        date_provider: Callable[[], datetime],
    ) -> None:
        self._hints_client = hints_client
        self._search_client = search_client
        self._date_provider = date_provider
        self._cached_genre_ids: dict[str, int | None] = {}

    @property
    def last_diagnostics(self) -> ResearchDiagnostics | None:
        """Immutable diagnostics for this service's latest run in the current async task."""
        current = _current_diagnostics.get()
        if current is None or current[0] is not self:
            return None
        return current[1].snapshot()

    async def resolve_genre_id(self, *, bundle_id: str) -> int | None:
        if bundle_id in self._cached_genre_ids:
            return self._cached_genre_ids[bundle_id]
        try:
            listing = await self._search_client.lookup(bundle_id=bundle_id, country="US")
        except Exception:  # noqa: BLE001 - genre resolution is best-effort
            listing = None
        genre_id = listing.primary_genre_id if listing is not None else None
        self._cached_genre_ids[bundle_id] = genre_id
        return genre_id

    async def _own_brand_key(self, bundle_id: str, country: str) -> str | None:
        try:
            listing = await self._search_client.lookup(bundle_id=bundle_id, country=country)
            _record_diagnostic("scoring")
        except Exception as error:  # noqa: BLE001 - best-effort
            _record_diagnostic("scoring", error)
            return None
        if listing is None:
            return None
        return _brand_key(listing.track_name)

    async def _safe_suggestions(self, term: str, country: str, stage: str = "expanding") -> list[str]:
        try:
            result = await self._hints_client.suggestions(term=term, country=country)
            _record_diagnostic(stage)
            return result
        except Exception as error:  # noqa: BLE001 - one bad seed must not abort the run
            _record_diagnostic(stage, error)
            return []

    async def _expand(
        self, seeds: list[str], country: str, locale_id: str, on_progress: ProgressCallback | None
    ) -> dict[str, int]:
        best_position: dict[str, int] = {}
        completed = 0
        for chunk in _chunked(seeds, MAX_CONCURRENCY):
            results = await asyncio.gather(*(self._safe_suggestions(seed, country) for seed in chunk))
            for hints in results:
                for position, term in enumerate(hints):
                    cleaned = term.strip()
                    if len(cleaned) < 2:
                        continue
                    if cleaned not in best_position or position < best_position[cleaned]:
                        best_position[cleaned] = position
            completed += len(chunk)
            if on_progress:
                on_progress(ResearchProgress(locale_id, ResearchStage.EXPANDING, completed, len(seeds)))
        return best_position

    async def _measure_prefix_depth(
        self, terms: list[str], country: str, locale_id: str, on_progress: ProgressCallback | None
    ) -> dict[str, float]:
        ratios: dict[str, float] = {}
        completed = 0
        for term in terms:
            characters = list(term)
            if len(characters) < 3:
                completed += 1
                continue

            lengths = []
            for fraction in (0.4, 0.6, 0.8):
                length = round(len(characters) * fraction)
                length = max(1, min(length, len(characters) - 1))
                lengths.append(length)

            probed: set[int] = set()
            for length in lengths[:MAX_LADDER_PROBES]:
                if length in probed:
                    continue
                probed.add(length)
                prefix = "".join(characters[:length])
                hints = await self._safe_suggestions(prefix, country, "scoring")
                surfaced = any(_normalise(h) == _normalise(term) for h in hints)
                if surfaced:
                    ratios[term] = length / len(characters)
                    break

            completed += 1
            if on_progress:
                on_progress(ResearchProgress(locale_id, ResearchStage.SCORING, completed, len(terms)))
        return ratios

    async def _qualify_one(
        self, term: str, country: str, style: KeywordFieldStyle, target_genre_id: int | None, bundle_id: str | None
    ) -> tuple[str, _TermOutcome | None]:
        try:
            listings = await self._search_client.search(term=term, country=country, limit=SEARCH_DEPTH)
            _record_diagnostic("harvesting")
        except Exception as error:  # noqa: BLE001 - best-effort
            _record_diagnostic("harvesting", error)
            return term, None

        unique_harvested: list[_HarvestedToken] = []
        for listing in listings[:10]:
            on_category = target_genre_id is None or listing.primary_genre_id == target_genre_id
            for token in candidate_terms(listing.track_name, style=style):
                unique_harvested.append(_HarvestedToken(token, listing.track_id, on_category))

        our_rank = None
        if bundle_id is not None:
            for index, listing in enumerate(listings):
                if listing.bundle_id == bundle_id:
                    our_rank = index + 1
                    break

        brand_keys = {key for listing in listings[:10] if (key := _brand_key(listing.track_name))}
        category_names = [
            listing.primary_genre_name
            for listing in listings[:20]
            if listing.primary_genre_name
            and (target_genre_id is None or listing.primary_genre_id == target_genre_id)
        ]

        outcome = _TermOutcome(
            relevance=relevance(
                result_genre_ids=[listing.primary_genre_id for listing in listings[:20]],
                target_genre_id=target_genre_id,
            ),
            competition=competition(
                top_rating_counts=[
                    listing.user_rating_count for listing in listings[:10] if listing.user_rating_count is not None
                ]
            ),
            our_rank=our_rank,
            harvested=unique_harvested,
            brand_keys=brand_keys,
            category_names=category_names,
            sample_size=min(len(listings), 10),
            listings=listings,
        )
        return term, outcome

    async def _qualify(
        self,
        terms: list[str],
        country: str,
        locale_id: str,
        style: KeywordFieldStyle,
        target_genre_id: int | None,
        bundle_id: str | None,
        on_progress: ProgressCallback | None,
    ) -> dict[str, _TermOutcome]:
        outcomes: dict[str, _TermOutcome] = {}
        completed = 0
        for chunk in _chunked(terms, MAX_CONCURRENCY):
            results = await asyncio.gather(
                *(self._qualify_one(term, country, style, target_genre_id, bundle_id) for term in chunk)
            )
            for term, outcome in results:
                if outcome is not None:
                    outcomes[term] = outcome
            completed += len(chunk)
            if on_progress:
                on_progress(ResearchProgress(locale_id, ResearchStage.HARVESTING, completed, len(terms)))
        return outcomes

    async def research(
        self,
        *,
        locale_id: str,
        seeds: list[str],
        bundle_id: str | None,
        target_genre_id: int | None,
        known_brand_names: list[str] | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> KeywordPool:
        _current_diagnostics.set((self, _DiagnosticsCollector()))
        return await self._research_run(
            locale_id=locale_id, seeds=seeds, bundle_id=bundle_id, target_genre_id=target_genre_id,
            known_brand_names=known_brand_names, on_progress=on_progress,
        )

    async def _research_run(
        self,
        *,
        locale_id: str,
        seeds: list[str],
        bundle_id: str | None,
        target_genre_id: int | None,
        known_brand_names: list[str] | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> KeywordPool:
        now = self._date_provider()

        mapping = mapping_for_locale(locale_id)
        if mapping is None:
            if on_progress:
                on_progress(ResearchProgress(locale_id, ResearchStage.FINISHED, 0, 0))
            return _ungrounded_pool(locale_id, now)

        style = mapping.keyword_style
        usable_seeds = [s.strip() for s in seeds if len(s.strip()) >= 2][:MAX_SEEDS]
        if not usable_seeds:
            if on_progress:
                on_progress(ResearchProgress(locale_id, ResearchStage.FINISHED, 0, 0))
            return _ungrounded_pool(locale_id, now)

        best_position = await self._expand(usable_seeds, mapping.country_code, locale_id, on_progress)

        sources_by_term: dict[str, set[KeywordSource]] = {}
        canonical_source_terms: dict[str, str] = {}

        def add_source(term: str, source: KeywordSource) -> None:
            key = _normalise(term)
            if not key:
                return
            surface = canonical_source_terms.setdefault(key, term)
            sources_by_term.setdefault(surface, set()).add(source)

        for seed in usable_seeds:
            add_source(seed, KeywordSource.SEED)
        for term in best_position:
            add_source(term, KeywordSource.AUTOCOMPLETE)

        if not best_position and not sources_by_term:
            if on_progress:
                on_progress(ResearchProgress(locale_id, ResearchStage.FINISHED, 0, 0))
            return _ungrounded_pool(locale_id, now)

        def rank_key(term: str) -> tuple[float, int, str]:
            return (best_position.get(term, float("inf")), len(term), term)

        ranked = sorted(sources_by_term.keys(), key=rank_key)
        candidates = ranked[:MAX_EXPANDED_TERMS]

        ladder_terms = candidates[:MAX_LADDER_TERMS]
        prefix_ratios = await self._measure_prefix_depth(ladder_terms, mapping.country_code, locale_id, on_progress)

        qualify_terms = candidates[:MAX_QUALIFIED_TERMS]
        qualified = await self._qualify(
            qualify_terms, mapping.country_code, locale_id, style, target_genre_id, bundle_id, on_progress
        )

        competitor_frequency: dict[str, int] = {}
        competitor_sample = 0
        category_counts: dict[str, int] = {}
        users_by_token: dict[str, set[int]] = {}
        on_category_users_by_token: dict[str, set[int]] = {}
        results_by_term: list[list[StorefrontAppListing]] = []
        canonical_form: dict[str, str] = {}

        def canonical(token: str) -> str:
            key = _normalise(token)
            existing = canonical_form.get(key)
            if existing is not None:
                return existing
            canonical_form[key] = token
            return token

        for term in qualify_terms:
            outcome = qualified.get(term)
            if outcome is None:
                continue
            competitor_sample = max(competitor_sample, outcome.sample_size)
            results_by_term.append(outcome.listings)
            for name in outcome.category_names:
                category_counts[name] = category_counts.get(name, 0) + 1
            for token in outcome.harvested:
                form = canonical(token.term)
                users_by_token.setdefault(form, set()).add(token.track_id)
                if token.is_on_category:
                    on_category_users_by_token.setdefault(form, set()).add(token.track_id)

            counted_this_term: set[str] = set()
            for token in outcome.harvested:
                form = canonical(token.term)
                if form in counted_this_term:
                    continue
                counted_this_term.add(form)
                competitor_frequency[form] = competitor_frequency.get(form, 0) + 1

        ranking = rank(results_by_term=results_by_term, target_genre_id=target_genre_id, own_bundle_id=bundle_id)
        brand_keys = set(ranking.exclusive_brand_keys)
        hint_brand_keys: set[str] = set()
        for hint in known_brand_names or []:
            key = _brand_key(hint) or _normalise(hint.strip())
            if key:
                brand_keys.add(key)
                hint_brand_keys.add(key)
        if bundle_id is not None:
            own_brand = await self._own_brand_key(bundle_id, mapping.country_code)
            if own_brand:
                brand_keys.discard(own_brand)

        for competitor_term in competitor_frequency:
            add_source(competitor_term, KeywordSource.COMPETITOR)

        def has_brand_key(term: str, sources: set[KeywordSource] | None = None) -> bool:
            key = _normalise(term)
            if key in brand_keys:
                return True
            # A multi-word seed may share a generic token with an app title; only use
            # token-level autocomplete extraction for single-word surfaces.
            if sources is not None and KeywordSource.SEED in sources:
                return False
            return bool(indexed_tokens(term, style=style) & brand_keys)

        def harvested_relevance(term: str) -> int | None:
            users = users_by_token.get(term)
            if not users:
                return None
            on_category = len(on_category_users_by_token.get(term, set()))
            return round(on_category / len(users) * 100)

        terms: list[KeywordTerm] = []
        for term, sources in sources_by_term.items():
            if not _locale_term_allowed(term, sources, style):
                continue
            outcome = qualified.get(term)
            term_popularity = popularity(
                hint_position=best_position.get(term),
                prefix_ratio=prefix_ratios.get(term),
                competitor_frequency=competitor_frequency.get(term, 0),
                competitor_sample_size=competitor_sample,
            )
            if term_popularity <= 0:
                continue
            # One app using a word is not evidence a market searches it — see the module docstring
            # and KeywordTerm.field_priority for why unverified terms must never be treated as real.
            is_corroborated_by_market = (
                term in best_position
                or has_brand_key(term)
                or len(users_by_token.get(term, set())) >= MINIMUM_COMPETITOR_APPS
            )
            if not is_corroborated_by_market:
                continue
            if outcome is not None and outcome.relevance <= 0:
                continue
            terms.append(
                KeywordTerm(
                    term=term,
                    sources=set(sources),
                    popularity=term_popularity,
                    competition=outcome.competition if outcome else 0,
                    relevance=outcome.relevance if outcome else harvested_relevance(term),
                    our_rank=outcome.our_rank if outcome else None,
                    is_brand_term=has_brand_key(term, sources) and KeywordSource.SEED not in sources,
                )
            )

        for app in ranking.brand_candidates:
            if not app.brand:
                continue
            if not _locale_term_allowed(app.brand, {KeywordSource.COMPETITOR}, style):
                continue
            key = _normalise(app.brand)
            if key not in brand_keys:
                continue
            # A brand seen in only one probed search is noise, not a competitor — hold brand
            # candidates to the same evidence bar as harvested terms (MINIMUM_COMPETITOR_APPS).
            # Client-supplied brand hints are trusted as named and skip this floor.
            if key not in hint_brand_keys and app.appearances < MINIMUM_COMPETITOR_APPS:
                continue
            # Off-category apps get dragged in by noisy seeds and would otherwise score a flat
            # relevance=100. Score them against the app's own genre like every other term; a
            # known target genre with a genre mismatch (relevance 0) drops the candidate. When
            # the genre is unknown this stays 100, preserving prior behaviour.
            brand_relevance = relevance(
                result_genre_ids=[app.primary_genre_id], target_genre_id=target_genre_id
            )
            if key not in hint_brand_keys and target_genre_id is not None and brand_relevance == 0:
                continue
            if brand_relevance <= 0:
                continue
            brand_pop = brand_popularity(
                appearances=app.appearances, search_count=len(qualify_terms), user_rating_count=app.user_rating_count
            )
            if brand_pop <= 0:
                continue
            terms.append(
                KeywordTerm(
                    term=app.brand,
                    sources={KeywordSource.COMPETITOR},
                    popularity=brand_pop,
                    competition=competition(top_rating_counts=[app.user_rating_count or 0]),
                    relevance=brand_relevance,
                    our_rank=None,
                    is_brand_term=True,
                )
            )

        # Harvested terms and ranked brand candidates can describe the same surface term.
        # Merge them by the tokenizer's canonical key so scoring and selected-brand reporting
        # cannot double-count one keyword.
        unique_terms: dict[str, KeywordTerm] = {}
        for keyword_term in terms:
            key = _normalise(keyword_term.term)
            previous = unique_terms.get(key)
            if previous is None:
                unique_terms[key] = keyword_term
                continue
            unique_terms[key] = KeywordTerm(
                term=previous.term,
                sources=previous.sources | keyword_term.sources,
                popularity=max(previous.popularity, keyword_term.popularity),
                competition=max(previous.competition, keyword_term.competition),
                relevance=max(previous.relevance or 0, keyword_term.relevance or 0) or None,
                our_rank=min(
                    rank for rank in (previous.our_rank, keyword_term.our_rank) if rank is not None
                ) if previous.our_rank is not None or keyword_term.our_rank is not None else None,
                is_brand_term=previous.is_brand_term or keyword_term.is_brand_term,
            )
        terms = list(unique_terms.values())

        if on_progress:
            on_progress(ResearchProgress(locale_id, ResearchStage.FINISHED, len(terms), len(terms)))

        achieved = (
            mapping.grounding
            if any(
                KeywordSource.SEED in t.sources
                and t.is_corroborated
                and t.popularity > 0
                and (t.relevance or 0) > 0
                for t in terms
            )
            else GroundingExpectation.NONE
        )

        category_name = None
        if category_counts:
            max_count = max(category_counts.values())
            # Tie-break picks the lexicographically SMALLEST name — see
            # reference/swift/Services/ASO/ASOResearchService.swift's categoryCounts.max{}
            # comparator (`lhs.key > rhs.key` as the "increasing order" predicate on ties).
            tied = [name for name, count in category_counts.items() if count == max_count]
            category_name = min(tied)

        return KeywordPool(
            locale_id=locale_id,
            country_code=mapping.country_code,
            terms=terms,
            grounding=achieved,
            keyword_style=style,
            category_name=category_name,
            top_apps=ranking.apps,
            researched_at=now,
        )
