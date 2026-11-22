"""Run the standard ASO research workflow over client-collected Apple evidence.

This adapter deliberately has no network client. Missing evidence behaves like an empty
response, allowing the normal research service to degrade without making an upstream call.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from asogenic.aso.research_service import MAX_HINT_REQUESTS_PER_LOCALE, ASOResearchService
from asogenic.errors import ASOgenicError, validation_error
from asogenic.models.keywords import KeywordPool
from asogenic.models.storefront_listing import StorefrontAppListing

MAX_EVIDENCE_BYTES = 256 * 1024
MAX_QUERY_ENTRIES = 64
MAX_LOOKUP_ENTRIES = 8
MAX_RESULTS_PER_RESPONSE = 50
MAX_HINTS_PER_RESPONSE = 50
MAX_TOTAL_RESULTS = 500
MAX_TOTAL_HINTS = 500


class EvidenceValidationError(ASOgenicError):
    """Typed validation error raised for malformed or oversized client evidence."""


def _invalid(message: str, field: str = "evidence") -> EvidenceValidationError:
    error = validation_error(message, field_name=field)
    return EvidenceValidationError(**error.__dict__)


def _check_integer(value: Any, field: str, *, minimum: int = 0, maximum: int = 2**31 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise _invalid(f"{field} must be an integer between {minimum} and {maximum}", field)
    return value


def _optional_string(item: dict[str, Any], key: str, *, max_length: int = 20_000) -> str | None:
    value = item.get(key)
    if value is not None and (not isinstance(value, str) or len(value) > max_length):
        raise _invalid(f"{key} must be a string of at most {max_length} characters", f"evidence.{key}")
    return value


def _parse_listings(payload: Any, field: str) -> list[StorefrontAppListing]:
    if not isinstance(payload, dict) or set(payload) - {"results", "resultCount"} or "results" not in payload:
        raise _invalid("Apple search/lookup response must contain a results array", field)
    if "resultCount" in payload:
        _check_integer(payload["resultCount"], f"{field}.resultCount")
    results = payload["results"]
    if not isinstance(results, list) or len(results) > MAX_RESULTS_PER_RESPONSE:
        raise _invalid(f"results must be an array of at most {MAX_RESULTS_PER_RESPONSE} entries", field)
    listings: list[StorefrontAppListing] = []
    for index, item in enumerate(results):
        at = f"{field}.results[{index}]"
        if not isinstance(item, dict):
            raise _invalid("result must be an object", at)
        if "trackId" not in item:
            raise _invalid("result is missing trackId", at)
        name = item.get("trackName", "")
        if not isinstance(name, str) or len(name) > 512:
            raise _invalid("trackName must be a string of at most 512 characters", f"{at}.trackName")
        track_id = _check_integer(item["trackId"], f"{at}.trackId", minimum=1)
        genre_id = item.get("primaryGenreId")
        if genre_id is not None:
            genre_id = _check_integer(genre_id, f"{at}.primaryGenreId", minimum=1)
        rating_count = item.get("userRatingCount")
        if rating_count is not None:
            rating_count = _check_integer(rating_count, f"{at}.userRatingCount")
        rating = item.get("averageUserRating")
        if rating is not None and (
            isinstance(rating, bool) or not isinstance(rating, (int, float)) or not 0 <= rating <= 5
        ):
            raise _invalid("averageUserRating must be a number between 0 and 5", f"{at}.averageUserRating")
        listings.append(StorefrontAppListing(
            track_id=track_id,
            track_name=name,
            bundle_id=_optional_string(item, "bundleId", max_length=255),
            description=_optional_string(item, "description"),
            primary_genre_id=genre_id,
            primary_genre_name=_optional_string(item, "primaryGenreName", max_length=255),
            user_rating_count=rating_count,
            average_user_rating=float(rating) if rating is not None else None,
        ))
    return listings


def _validate_evidence(evidence: dict[str, Any]) -> tuple[dict[str, list[str]], dict[str, list[StorefrontAppListing]], dict[str, StorefrontAppListing | None]]:
    if not isinstance(evidence, dict):
        raise _invalid("must be a JSON object")
    try:
        encoded = json.dumps(evidence, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise _invalid("must contain only valid JSON values") from exc
    if len(encoded) > MAX_EVIDENCE_BYTES:
        raise _invalid(f"encoded evidence exceeds {MAX_EVIDENCE_BYTES} bytes")
    if set(evidence) - {"hints", "searches", "lookups"} or not {"hints", "searches"} <= set(evidence):
        raise _invalid("must contain hints and searches, with optional lookups")

    hints_raw = evidence["hints"]
    searches_raw = evidence["searches"]
    lookups_raw = evidence.get("lookups", {})
    for key, mapping, limit in (
        ("hints", hints_raw, MAX_QUERY_ENTRIES),
        ("searches", searches_raw, MAX_QUERY_ENTRIES),
        ("lookups", lookups_raw, MAX_LOOKUP_ENTRIES),
    ):
        if not isinstance(mapping, dict) or len(mapping) > limit:
            raise _invalid(f"{key} must be an object with at most {limit} entries", f"evidence.{key}")
        if any(not isinstance(k, str) or not k or len(k) > 255 for k in mapping):
            raise _invalid("keys must be non-empty strings of at most 255 characters", f"evidence.{key}")

    hints: dict[str, list[str]] = {}
    total_hints = 0
    for query, payload in hints_raw.items():
        field = f"evidence.hints.{query}"
        if not isinstance(payload, dict) or "hints" not in payload or not isinstance(payload["hints"], list):
            raise _invalid("Apple hints response must contain a hints array", field)
        rows = payload["hints"]
        if len(rows) > MAX_HINTS_PER_RESPONSE:
            raise _invalid(f"hints array exceeds {MAX_HINTS_PER_RESPONSE} entries", field)
        terms: list[str] = []
        for i, row in enumerate(rows):
            at = f"{field}.hints[{i}]"
            if not isinstance(row, dict) or not isinstance(row.get("term"), str) or len(row["term"]) > 512:
                raise _invalid("hint must contain a string term of at most 512 characters", at)
            terms.append(row["term"])
        total_hints += len(terms)
        hints[query] = terms
    if total_hints > MAX_TOTAL_HINTS or len(hints) > MAX_HINT_REQUESTS_PER_LOCALE:
        raise _invalid("hint evidence exceeds the per-locale request or total-hint limit", "evidence.hints")

    searches: dict[str, list[StorefrontAppListing]] = {}
    total_results = 0
    for query, payload in searches_raw.items():
        parsed = _parse_listings(payload, f"evidence.searches.{query}")
        total_results += len(parsed)
        searches[query] = parsed

    lookups: dict[str, StorefrontAppListing | None] = {}
    for bundle, payload in lookups_raw.items():
        parsed = _parse_listings(payload, f"evidence.lookups.{bundle}")
        if len(parsed) > 1:
            raise _invalid("lookup response may contain at most one result", f"evidence.lookups.{bundle}")
        lookups[bundle] = parsed[0] if parsed else None

    if total_results > MAX_TOTAL_RESULTS:
        raise _invalid(f"total search/lookup results exceed {MAX_TOTAL_RESULTS}", "evidence.searches")
    return hints, searches, lookups


class _EvidenceHintsClient:
    def __init__(self, hints: dict[str, list[str]]) -> None:
        self._hints = hints

    async def suggestions(self, *, term: str, country: str) -> list[str]:
        return list(self._hints.get(term, []))


class _EvidenceSearchClient:
    def __init__(self, searches: dict[str, list[StorefrontAppListing]], lookups: dict[str, StorefrontAppListing | None]) -> None:
        self._searches = searches
        self._lookups = lookups

    async def search(self, *, term: str, country: str, limit: int) -> list[StorefrontAppListing]:
        return list(self._searches.get(term, [])[:limit])

    async def lookup(self, *, bundle_id: str, country: str) -> StorefrontAppListing | None:
        return self._lookups.get(bundle_id)


async def research_from_evidence(
    *,
    locale_id: str,
    seeds: list[str],
    target_genre_id: int | None,
    bundle_id: str,
    known_brand_names: list[str],
    evidence: dict[str, Any],
    date_provider: Callable[[], datetime],
) -> KeywordPool:
    """Return the normal scored pool using only bounded client-supplied Apple response data."""
    hints, searches, lookups = _validate_evidence(evidence)
    if not isinstance(bundle_id, str) or not bundle_id:
        raise _invalid("bundle_id must be a non-empty string", "bundle_id")
    service = ASOResearchService(
        hints_client=_EvidenceHintsClient(hints),
        search_client=_EvidenceSearchClient(searches, lookups),
        date_provider=date_provider,
    )
    return await service.research(
        locale_id=locale_id,
        seeds=seeds,
        bundle_id=bundle_id,
        target_genre_id=target_genre_id,
        known_brand_names=known_brand_names,
    )
