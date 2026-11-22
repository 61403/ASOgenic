"""One app as returned by the public iTunes Search/Lookup API. Port of
reference/swift/Services/ASO/StorefrontAppListing.swift."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StorefrontAppListing:
    track_id: int
    track_name: str
    bundle_id: str | None = None
    description: str | None = None
    primary_genre_id: int | None = None
    primary_genre_name: str | None = None
    user_rating_count: int | None = None
    average_user_rating: float | None = None
