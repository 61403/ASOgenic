"""AIMD per-minute request throttle. Port of
reference/swift/Services/ASO/RequestThrottler.swift.

Starts at a documented-safe rate and adapts: additive increase on sustained success,
multiplicative decrease the instant the server signals 429/403 - same shape as TCP congestion
control.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable


class _AIMDState:
    """Shared bookkeeping for the additive-increase/multiplicative-decrease algorithm. Holds no
    sleep primitive of its own — RequestThrottler and SyncRequestThrottler each drive it with
    their own (async vs. blocking) wait, since a single caller can't await a sync client's
    request or block an event loop's, and the algorithm itself is identical either way."""

    def __init__(
        self,
        *,
        starting_requests_per_minute: int,
        min_requests_per_minute: int,
        max_requests_per_minute: int,
        ramp_up_step: int,
        ramp_up_threshold: int,
        clock: Callable[[], float],
    ) -> None:
        self._min = min(min_requests_per_minute, max_requests_per_minute)
        self._max = max_requests_per_minute
        self._ramp_up_step = ramp_up_step
        self._ramp_up_threshold = ramp_up_threshold
        self._clock = clock

        self._current_limit = min(starting_requests_per_minute, max_requests_per_minute)
        self._request_timestamps: list[float] = []
        self._consecutive_successes = 0

    @property
    def current_requests_per_minute(self) -> int:
        return self._current_limit

    def _try_reserve_slot(self) -> float | None:
        """Returns None once a slot is reserved; otherwise the number of seconds the caller
        should wait before trying again."""
        now = self._clock()
        self._request_timestamps = [t for t in self._request_timestamps if now - t < 60]

        if len(self._request_timestamps) < self._current_limit:
            self._request_timestamps.append(now)
            return None

        oldest = self._request_timestamps[0]
        jitter = random.uniform(0.05, 0.3)
        return max(60 - (now - oldest) + jitter, 0.1)

    def report_success(self) -> None:
        self._consecutive_successes += 1
        if self._consecutive_successes >= self._ramp_up_threshold and self._current_limit < self._max:
            self._current_limit = min(self._current_limit + self._ramp_up_step, self._max)
            self._consecutive_successes = 0

    def report_throttled(self) -> None:
        self._current_limit = max(self._current_limit // 2, self._min)
        self._consecutive_successes = 0


class RequestThrottler:
    def __init__(
        self,
        *,
        starting_requests_per_minute: int = 20,
        min_requests_per_minute: int = 10,
        max_requests_per_minute: int = 60,
        ramp_up_step: int = 5,
        ramp_up_threshold: int = 30,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._state = _AIMDState(
            starting_requests_per_minute=starting_requests_per_minute,
            min_requests_per_minute=min_requests_per_minute,
            max_requests_per_minute=max_requests_per_minute,
            ramp_up_step=ramp_up_step,
            ramp_up_threshold=ramp_up_threshold,
            clock=clock,
        )
        self._sleep = sleep or (lambda seconds: asyncio.sleep(seconds))

    @property
    def current_requests_per_minute(self) -> int:
        return self._state.current_requests_per_minute

    async def wait_for_slot(self) -> None:
        while True:
            wait_interval = self._state._try_reserve_slot()
            if wait_interval is None:
                return
            await self._sleep(wait_interval)

    def report_success(self) -> None:
        self._state.report_success()

    def report_throttled(self) -> None:
        self._state.report_throttled()


class SyncRequestThrottler:
    """Blocking twin of RequestThrottler, same AIMD algorithm, for callers (like ASCClient) built
    on a synchronous httpx.Client rather than asyncio — the ASC write path predates the research
    clients' async throttling and converting its whole sync call surface to async just to reuse
    `await wait_for_slot()` would be a far bigger change than throttling itself."""

    def __init__(
        self,
        *,
        starting_requests_per_minute: int = 20,
        min_requests_per_minute: int = 10,
        max_requests_per_minute: int = 60,
        ramp_up_step: int = 5,
        ramp_up_threshold: int = 30,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._state = _AIMDState(
            starting_requests_per_minute=starting_requests_per_minute,
            min_requests_per_minute=min_requests_per_minute,
            max_requests_per_minute=max_requests_per_minute,
            ramp_up_step=ramp_up_step,
            ramp_up_threshold=ramp_up_threshold,
            clock=clock,
        )
        self._sleep = sleep or time.sleep

    @property
    def current_requests_per_minute(self) -> int:
        return self._state.current_requests_per_minute

    def wait_for_slot(self) -> None:
        while True:
            wait_interval = self._state._try_reserve_slot()
            if wait_interval is None:
                return
            self._sleep(wait_interval)

    def report_success(self) -> None:
        self._state.report_success()

    def report_throttled(self) -> None:
        self._state.report_throttled()
