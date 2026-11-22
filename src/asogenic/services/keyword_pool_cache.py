"""Caches researched KeywordPools so a run doesn't re-pay for research already done. Port of
reference/swift/Services/KeywordPoolCacheService.swift, adapted to an in-memory store.

Research costs dozens of throttled requests and runs for minutes; a 14-day TTL is generous
relative to how fast real search terms move. Keyed by (bundle_id, locale_id) — bundle ID rather
than a version-scoped session key, since research describes the market, not a particular app
version.

This is an in-memory-only first pass (this is a stdio MCP server, not a long-lived daemon, so
process-lifetime caching already avoids the common case of re-researching within one session).
A future enhancement could persist to disk (e.g. under XDG_CACHE_HOME) to survive restarts,
mirroring the Swift service's Application Support JSON files.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from asogenic.models.keywords import CURRENT_POOL_VERSION, KeywordPool

TIME_TO_LIVE_SECONDS = 14 * 24 * 60 * 60


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class _CacheKey:
    bundle_id: str
    locale_id: str
    request_fingerprint: str = "legacy"


class KeywordPoolCacheService:
    def __init__(self, *, date_provider: Callable[[], datetime] | None = None) -> None:
        self._date_provider = date_provider or _utc_now
        self._store: dict[_CacheKey, KeywordPool] = {}

    def load(self, *, bundle_id: str, locale_id: str, request_fingerprint: str = "legacy") -> KeywordPool | None:
        """The cached pool, or None when absent, stale-versioned, or expired. An expired or
        stale-version entry is evicted on read."""
        key = _CacheKey(bundle_id, locale_id, request_fingerprint)
        pool = self._store.get(key)
        if pool is None and request_fingerprint == "legacy":
            matches = [value for cache_key, value in self._store.items() if cache_key.bundle_id == bundle_id and cache_key.locale_id == locale_id]
            if len(matches) == 1:
                pool = matches[0]
        if pool is None and request_fingerprint != "legacy":
            # Compatibility for pools written by pre-v2 callers/tests. New producer calls always
            # use the request-specific key and therefore never collide with one another.
            pool = self._store.get(_CacheKey(bundle_id, locale_id, "legacy"))
        if pool is None:
            return None

        if pool.pool_version < CURRENT_POOL_VERSION:
            del self._store[key]
            return None

        age_seconds = (self._date_provider() - pool.researched_at).total_seconds()
        if age_seconds >= TIME_TO_LIVE_SECONDS:
            del self._store[key]
            return None

        return pool

    def save(self, pool: KeywordPool, *, bundle_id: str, request_fingerprint: str = "legacy") -> None:
        self._store[_CacheKey(bundle_id, pool.locale_id, request_fingerprint)] = pool

    def delete(self, *, bundle_id: str, locale_id: str) -> None:
        for key in [k for k in self._store if k.bundle_id == bundle_id and k.locale_id == locale_id]:
            del self._store[key]

    def delete_all(self, *, bundle_id: str) -> None:
        for key in [k for k in self._store if k.bundle_id == bundle_id]:
            del self._store[key]
