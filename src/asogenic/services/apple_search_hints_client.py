"""Apple's undocumented MZSearchHints autocomplete endpoint. Port of
reference/swift/Services/ASO/AppleSearchHintsClient.swift.

Read-only, throttled, single attempt with no retry, never load-bearing — callers swallow every
failure and degrade to a smaller/ungrounded pool. The response is an XML property list, not
JSON. A missing header, an unknown term, and an unmapped storefront all return HTTP 200 with an
empty hints array - never rely on a non-2xx to detect misconfiguration.
"""

from __future__ import annotations

import plistlib

import httpx

from asogenic.aso.network_errors import (
    DecodingError,
    HTTPStatusError,
    TransportError,
    UnsupportedStorefrontError,
)
from asogenic.aso.storefront_ids import storefront_id_for_country
from asogenic.services.request_throttler import RequestThrottler

BASE_URL = "https://search.itunes.apple.com/WebObjects/MZSearchHints.woa/wa/hints"
STARTING_REQUESTS_PER_MINUTE = 20


class AppleSearchHintsClient:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        throttler: RequestThrottler | None = None,
        base_url: str = BASE_URL,
    ) -> None:
        self._http = http or httpx.AsyncClient(timeout=15.0)
        self._throttler = throttler or RequestThrottler(
            starting_requests_per_minute=STARTING_REQUESTS_PER_MINUTE,
            min_requests_per_minute=5,
            max_requests_per_minute=30,
        )
        self._base_url = base_url

    async def suggestions(self, *, term: str, country: str) -> list[str]:
        storefront_id = storefront_id_for_country(country)
        if storefront_id is None:
            raise UnsupportedStorefrontError(country)

        await self._throttler.wait_for_slot()

        try:
            response = await self._http.get(
                self._base_url,
                params={"clientApplication": "Software", "term": term},
                headers={"X-Apple-Store-Front": str(storefront_id)},
            )
        except httpx.HTTPError as exc:
            raise TransportError(str(exc)) from exc

        if response.status_code in (429, 403):
            self._throttler.report_throttled()
            raise HTTPStatusError(response.status_code)

        if not (200 <= response.status_code < 300):
            raise HTTPStatusError(response.status_code)

        self._throttler.report_success()

        try:
            decoded = plistlib.loads(response.content)
            hints = decoded.get("hints", [])
            return [h["term"] for h in hints if isinstance(h, dict) and "term" in h]
        except Exception as exc:
            raise DecodingError(str(exc)) from exc
