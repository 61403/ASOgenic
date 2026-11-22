"""Keyword research data model. Source: docs/interfaces/data-model.md #KeywordSource..CoverageReport,
reference/swift/Models/KeywordTerm.swift, KeywordPool.swift, RankedApp.swift."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime

from asogenic.aso.keyword_scoring import competition_multiplier
from asogenic.aso.storefront_catalog import GroundingExpectation, KeywordFieldStyle

# Bumped 2 -> 3 when the wire shape in tools/pool_serde.py dropped track_id/bundle_id/
# researched_at/pool_version/country_code and slimmed CoverageReport's dropped/
# indexed_in_name_or_subtitle sections — a documented signal in case any external consumer
# persists the serialized JSON shape across a server upgrade. The in-memory KeywordPoolCacheService
# is unaffected (it caches live KeywordPool objects, not serialized dicts).
CURRENT_POOL_VERSION = 3


class KeywordSource(enum.Enum):
    AUTOCOMPLETE = "autocomplete"
    COMPETITOR = "competitor"
    SEED = "seed"
    SOURCE_LOCALE = "sourceLocale"
    TRANSLATED = "translated"

    @property
    def is_evidence(self) -> bool:
        return self in (KeywordSource.AUTOCOMPLETE, KeywordSource.COMPETITOR, KeywordSource.SOURCE_LOCALE)

    @property
    def is_fill(self) -> bool:
        return self in (KeywordSource.SOURCE_LOCALE, KeywordSource.TRANSLATED)


class KeywordDropReason(enum.Enum):
    BYTE_BUDGET = "byteBudget"
    DUPLICATE_OF_NAME_OR_SUBTITLE = "duplicateOfNameOrSubtitle"
    DUPLICATE_TERM = "duplicateTerm"
    CATEGORY_NAME = "categoryName"
    OWN_BRAND = "ownBrand"
    STYLE_RULE = "styleRule"
    LOW_RELEVANCE = "lowRelevance"
    BRAND_TERM_EXCLUDED = "brandTermExcluded"
    UNVERIFIED = "unverified"
    PLATFORM_TERM = "platformTerm"


@dataclass
class KeywordTerm:
    term: str
    sources: set[KeywordSource]
    popularity: int
    competition: int
    relevance: int | None
    our_rank: int | None
    is_brand_term: bool

    @property
    def byte_cost(self) -> int:
        return len(self.term.encode("utf-8")) + 1

    @property
    def is_corroborated(self) -> bool:
        return any(s.is_evidence for s in self.sources)

    @property
    def is_verified(self) -> bool:
        return self.relevance is not None

    @property
    def field_priority(self) -> float:
        # Unverified terms score zero (relevance None -> `relevance or 0` = 0): defaulting an
        # unchecked term to "perfectly relevant" is what let unverified junk outrank verified
        # terms in a live run. Never remove this.
        relevance_share = max(self.relevance or 0, 0) / 100
        return self.popularity * relevance_share * competition_multiplier(self.competition)


@dataclass(frozen=True)
class RankedApp:
    track_id: int
    bundle_id: str | None
    track_name: str
    brand: str | None
    average_user_rating: float | None
    user_rating_count: int | None
    appearances: int
    primary_genre_id: int | None = None


@dataclass
class KeywordPool:
    locale_id: str
    country_code: str
    terms: list[KeywordTerm]
    grounding: GroundingExpectation
    keyword_style: KeywordFieldStyle
    category_name: str | None
    top_apps: list[RankedApp]
    researched_at: datetime
    pool_version: int = CURRENT_POOL_VERSION

    @property
    def has_evidence(self) -> bool:
        return any(t.is_corroborated for t in self.terms)


@dataclass
class DroppedTerm:
    term: KeywordTerm
    reason: KeywordDropReason


@dataclass
class IdentityAlternative:
    name: str
    subtitle: str
    covered_popularity: int


@dataclass
class CoverageReport:
    locale_id: str
    covered: list[KeywordTerm]
    dropped: list[DroppedTerm]
    bytes_used: int
    grounding: GroundingExpectation
    indexed_in_name_or_subtitle: list[KeywordTerm] = field(default_factory=list)
    identity_alternatives: list[IdentityAlternative] = field(default_factory=list)

    @property
    def covered_popularity(self) -> int:
        return sum(t.popularity for t in self.covered)

    @property
    def is_grounded(self) -> bool:
        return self.grounding is not GroundingExpectation.NONE and bool(self.covered) and any(t.is_corroborated for t in self.covered)

    @property
    def describes_identity_only(self) -> bool:
        return not self.covered and bool(self.indexed_in_name_or_subtitle)
