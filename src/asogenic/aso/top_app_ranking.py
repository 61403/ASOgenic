"""Ranks the apps seen across every probed search. Port of
reference/swift/Services/ASO/TopAppRanking.swift."""

from __future__ import annotations

from dataclasses import dataclass

from asogenic.aso.listing_tokenizer import brand_segment, normalise
from asogenic.models.keywords import RankedApp
from asogenic.models.storefront_listing import StorefrontAppListing

LIMIT = 20
BRAND_CANDIDATE_LIMIT = 60
DEPTH = 25


@dataclass(frozen=True)
class TopAppRankingOutcome:
    apps: list[RankedApp]
    brand_candidates: list[RankedApp]
    exclusive_brand_keys: set[str]


def _populated_field_count(listing: StorefrontAppListing) -> int:
    return sum(
        1
        for present in (
            listing.bundle_id is not None,
            listing.primary_genre_id is not None,
            listing.user_rating_count is not None,
            listing.average_user_rating is not None,
        )
        if present
    )


def _richer(lhs: StorefrontAppListing, rhs: StorefrontAppListing) -> StorefrontAppListing:
    return rhs if _populated_field_count(rhs) > _populated_field_count(lhs) else lhs


def exclusive_brand_keys(apps: list[RankedApp]) -> set[str]:
    """Brand keys used by exactly one of these apps — the fix for treating a category phrase
    (e.g. "health tracker") as a brand."""
    titles = [normalise(app.track_name) for app in apps]
    result: set[str] = set()
    for app in apps:
        if not app.brand:
            continue
        key = normalise(app.brand)
        if not key:
            continue
        users = sum(1 for title in titles if key in title)
        if users == 1:
            result.add(key)
    return result


def rank(
    *,
    results_by_term: list[list[StorefrontAppListing]],
    target_genre_id: int | None,
    own_bundle_id: str | None,
    depth: int = DEPTH,
    limit: int = LIMIT,
) -> TopAppRankingOutcome:
    appearances: dict[int, int] = {}
    listing_by_track_id: dict[int, StorefrontAppListing] = {}
    best_position: dict[int, int] = {}

    for results in results_by_term:
        counted_this_term: set[int] = set()
        for position, listing in enumerate(results[:depth]):
            if own_bundle_id is not None and listing.bundle_id == own_bundle_id:
                continue
            if (
                target_genre_id is not None
                and listing.primary_genre_id is not None
                and listing.primary_genre_id != target_genre_id
            ):
                continue
            if listing.track_id in counted_this_term:
                continue
            counted_this_term.add(listing.track_id)

            appearances[listing.track_id] = appearances.get(listing.track_id, 0) + 1
            best_position[listing.track_id] = min(
                best_position.get(listing.track_id, position), position
            )
            existing = listing_by_track_id.get(listing.track_id)
            listing_by_track_id[listing.track_id] = (
                _richer(existing, listing) if existing is not None else listing
            )

    ranked_apps: list[RankedApp] = []
    for track_id in appearances:
        resolved_listing = listing_by_track_id.get(track_id)
        if resolved_listing is None:
            continue
        ranked_apps.append(
            RankedApp(
                track_id=track_id,
                bundle_id=resolved_listing.bundle_id,
                track_name=resolved_listing.track_name,
                brand=brand_segment(resolved_listing.track_name),
                average_user_rating=resolved_listing.average_user_rating,
                user_rating_count=resolved_listing.user_rating_count,
                appearances=appearances.get(track_id, 1),
                primary_genre_id=resolved_listing.primary_genre_id,
            )
        )

    def sort_key(app: RankedApp) -> tuple[int, int, int, int]:
        return (
            -app.appearances,
            -(app.user_rating_count or 0),
            best_position.get(app.track_id, 2**63 - 1),
            app.track_id,
        )

    ranked_apps.sort(key=sort_key)

    return TopAppRankingOutcome(
        apps=ranked_apps[:limit],
        brand_candidates=ranked_apps[:BRAND_CANDIDATE_LIMIT],
        exclusive_brand_keys=exclusive_brand_keys(ranked_apps),
    )
