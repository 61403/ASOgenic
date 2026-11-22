"""JSON-safe (de)serialization for KeywordPool/KeywordTerm/RankedApp/CoverageReport, since MCP
tool payloads are plain JSON and these models carry sets, enums, and datetimes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from asogenic.aso.storefront_catalog import GroundingExpectation, KeywordFieldStyle
from asogenic.models.keywords import (
    CURRENT_POOL_VERSION,
    CoverageReport,
    DroppedTerm,
    IdentityAlternative,
    KeywordDropReason,
    KeywordPool,
    KeywordSource,
    KeywordTerm,
    RankedApp,
)
from asogenic.release import info as release_info


def term_to_dict(term: KeywordTerm) -> dict[str, Any]:
    return {
        "term": term.term,
        "sources": sorted(s.value for s in term.sources),
        "popularity": term.popularity,
        "competition": term.competition,
        "relevance": term.relevance,
        "our_rank": term.our_rank,
        "is_brand_term": term.is_brand_term,
        "field_priority": round(term.field_priority, 2),
    }


def term_from_dict(data: dict[str, Any]) -> KeywordTerm:
    # `field_priority` in `data` (if present) is deliberately ignored: it's a derived @property on
    # KeywordTerm, not a constructor field, so it's always recomputed fresh from
    # popularity/relevance/competition below and can never drift out of sync or be spoofed via a
    # hand-edited wire dict.
    return KeywordTerm(
        term=data["term"],
        sources={KeywordSource(s) for s in data.get("sources", [])},
        popularity=data["popularity"],
        competition=data["competition"],
        relevance=data.get("relevance"),
        our_rank=data.get("our_rank"),
        is_brand_term=data.get("is_brand_term", False),
    )


def ranked_app_to_dict(app: RankedApp) -> dict[str, Any]:
    # track_id/bundle_id are internal identity used only during top_app_ranking.rank() (own-app
    # exclusion, dedup) — omitted here since no tool ever reads them back off a serialized pool.
    return {
        "track_name": app.track_name,
        "brand": app.brand,
        "average_user_rating": app.average_user_rating,
        "user_rating_count": app.user_rating_count,
        "appearances": app.appearances,
    }


def ranked_app_from_dict(data: dict[str, Any]) -> RankedApp:
    return RankedApp(
        track_id=data.get("track_id", 0),
        bundle_id=data.get("bundle_id"),
        track_name=data["track_name"],
        brand=data.get("brand"),
        average_user_rating=data.get("average_user_rating"),
        user_rating_count=data.get("user_rating_count"),
        appearances=data.get("appearances", 1),
    )


def pool_to_dict(pool: KeywordPool) -> dict[str, Any]:
    # researched_at/pool_version/country_code are carried on the internal KeywordPool for cache
    # TTL/versioning but never read by assemble_identity/assemble_keywords — omitted from the wire
    # shape the client has to round-trip through assemble_locale/optimize_source_search.
    return {
        "locale_id": pool.locale_id,
        "terms": [term_to_dict(t) for t in pool.terms],
        "grounding": pool.grounding.value,
        "keyword_style": pool.keyword_style.value,
        "category_name": pool.category_name,
        "top_apps": [ranked_app_to_dict(a) for a in pool.top_apps],
    }


def pool_from_dict(data: dict[str, Any]) -> KeywordPool:
    return KeywordPool(
        locale_id=data["locale_id"],
        country_code=data.get("country_code", ""),
        terms=[term_from_dict(t) for t in data.get("terms", [])],
        grounding=GroundingExpectation(data["grounding"]),
        keyword_style=KeywordFieldStyle(data["keyword_style"]),
        category_name=data.get("category_name"),
        top_apps=[ranked_app_from_dict(a) for a in data.get("top_apps", [])],
        researched_at=(
            datetime.fromisoformat(data["researched_at"])
            if "researched_at" in data
            else datetime.min.replace(tzinfo=UTC)
        ),
        pool_version=data.get("pool_version", CURRENT_POOL_VERSION),
    )


def dropped_term_to_dict(dropped: DroppedTerm) -> dict[str, Any]:
    # Slim on purpose: a dropped term isn't going anywhere, so the client only needs to know what
    # it was and why — not its full scored record.
    return {"term": dropped.term.term, "reason": dropped.reason.value}


def identity_alternative_to_dict(alt: IdentityAlternative) -> dict[str, Any]:
    return {"name": alt.name, "subtitle": alt.subtitle, "covered_popularity": alt.covered_popularity}


def coverage_report_to_dict(report: CoverageReport) -> dict[str, Any]:
    effective_grounding = report.grounding if report.covered and any(t.is_corroborated for t in report.covered) else GroundingExpectation.NONE
    return {
        **release_info(),
        "locale_id": report.locale_id,
        "covered": [term_to_dict(t) for t in report.covered],
        "dropped": [dropped_term_to_dict(d) for d in report.dropped],
        "bytes_used": report.bytes_used,
        "grounding": effective_grounding.value,
        # Just the term string: this means "already covered by name/subtitle, don't re-add it" —
        # no scores needed since it's not a keyword-field candidate.
        "indexed_in_name_or_subtitle": [t.term for t in report.indexed_in_name_or_subtitle],
        "identity_alternatives": [identity_alternative_to_dict(a) for a in report.identity_alternatives],
        "covered_popularity": report.covered_popularity,
        "is_grounded": effective_grounding is not GroundingExpectation.NONE,
    }


__all__ = [
    "KeywordDropReason",
    "coverage_report_to_dict",
    "dropped_term_to_dict",
    "identity_alternative_to_dict",
    "pool_from_dict",
    "pool_to_dict",
    "ranked_app_from_dict",
    "ranked_app_to_dict",
    "term_from_dict",
    "term_to_dict",
]
