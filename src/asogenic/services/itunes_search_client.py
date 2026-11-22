"""The public, unauthenticated iTunes Search/Lookup API. Port of
reference/swift/Services/ASO/ITunesSearchClient.swift.

Used for harvesting how top-ranking apps phrase their listing titles, checking a candidate
term's category fit, and finding where our own app currently ranks. Retries 429/403 up to
max_retries with 2^attempt backoff; transport timeouts/connection failures also count as
throttle signals.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from asogenic.aso.network_errors import (
    DecodingError,
    HTTPStatusError,
    RateLimitedError,
    TransportError,
)
from asogenic.models.storefront_listing import StorefrontAppListing
from asogenic.services.request_throttler import RequestThrottler

BASE_URL = "https://itunes.apple.com"

_OVERLOAD_EXCEPTIONS = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)


def _parse_listings(payload: dict[str, Any]) -> list[StorefrontAppListing]:
    results = payload.get("results", [])
    listings = []
    for item in results:
        listings.append(
            StorefrontAppListing(
                track_id=item["trackId"],
                track_name=item.get("trackName", ""),
                bundle_id=item.get("bundleId"),
                description=item.get("description"),
                primary_genre_id=item.get("primaryGenreId"),
                primary_genre_name=item.get("primaryGenreName"),
                user_rating_count=item.get("userRatingCount"),
                average_user_rating=item.get("averageUserRating"),
            )
        )
    return listings


class ITunesSearchClient:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        throttler: RequestThrottler | None = None,
        base_url: str = BASE_URL,
        max_retries: int = 3,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._http = http or httpx.AsyncClient(timeout=15.0)
        self._throttler = throttler or RequestThrottler()
        self._base_url = base_url
        self._max_retries = max_retries
        self._sleep = sleep or asyncio.sleep

    async def search(self, *, term: str, country: str, limit: int = 50) -> list[StorefrontAppListing]:
        params = {
            "term": term,
            "country": country,
            "entity": "software",
            "limit": str(min(max(limit, 1), 200)),
        }
        return await self._perform_request(f"{self._base_url}/search", params)

    async def lookup(self, *, bundle_id: str, country: str) -> StorefrontAppListing | None:
        params = {"bundleId": bundle_id, "country": country}
        results = await self._perform_request(f"{self._base_url}/lookup", params)
        return results[0] if results else None

    async def _perform_request(
        self, url: str, params: dict[str, str], *, retries_remaining: int | None = None
    ) -> list[StorefrontAppListing]:
        retries_remaining = self._max_retries if retries_remaining is None else retries_remaining
        await self._throttler.wait_for_slot()

        try:
            response = await self._http.get(url, params=params)
        except _OVERLOAD_EXCEPTIONS as exc:
            self._throttler.report_throttled()
            raise TransportError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise TransportError(str(exc)) from exc

        if response.status_code in (429, 403):
            self._throttler.report_throttled()
            if retries_remaining <= 0:
                raise RateLimitedError
            attempt = self._max_retries - retries_remaining
            await self._sleep(2**attempt)
            return await self._perform_request(url, params, retries_remaining=retries_remaining - 1)

        if not (200 <= response.status_code < 300):
            raise HTTPStatusError(response.status_code)

        self._throttler.report_success()

        try:
            return _parse_listings(response.json())
        except Exception as exc:
            raise DecodingError(str(exc)) from exc
