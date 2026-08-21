"""Fixed-window rate limiting.

The in-memory backend is process-local, which is fine for a single worker
but does NOT hold across replicas: each process keeps its own counters, so
N replicas allow roughly N times the limit. Swap in RedisRateLimiter (or
another shared store) before scaling out - the Protocol below is the seam.
"""

import threading
import time
from typing import Protocol


class RateLimiter(Protocol):
    def hit(self, key: str, limit: int, window_seconds: int) -> "RateLimitResult":
        """Record an attempt against `key` and report whether it is allowed."""
        ...


class RateLimitResult:
    __slots__ = ("allowed", "remaining", "retry_after")

    def __init__(self, allowed: bool, remaining: int, retry_after: int) -> None:
        self.allowed = allowed
        self.remaining = remaining
        self.retry_after = retry_after


class InMemoryRateLimiter:
    """Fixed-window counter kept in this process's memory.

    Counters are pruned lazily on access, so an idle key costs nothing after
    its window lapses and the dict cannot grow without bound under churn.
    """

    def __init__(self) -> None:
        # Each entry keeps the window length it was counted under, not just
        # when it started: routes have different windows (60s for login,
        # 300s for password reset) and pruning has to judge every entry
        # against its own.
        self._counters: dict[str, tuple[int, float, int]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = time.monotonic()

        with self._lock:
            count, window_start, _ = self._counters.get(key, (0, now, window_seconds))

            if now - window_start >= window_seconds:
                count, window_start = 0, now

            count += 1
            self._counters[key] = (count, window_start, window_seconds)

            if len(self._counters) > 10_000:
                self._prune(now)

        elapsed = now - window_start
        retry_after = max(1, int(window_seconds - elapsed))

        if count > limit:
            return RateLimitResult(allowed=False, remaining=0, retry_after=retry_after)
        return RateLimitResult(
            allowed=True, remaining=limit - count, retry_after=retry_after
        )

    def _prune(self, now: float) -> None:
        """Drop entries whose own window has lapsed.

        Judging every entry against the window of whichever request happened
        to trigger the prune would clear counters early — a burst of login
        hits (60s window) would wipe a caller's half-spent password-reset
        budget (300s), handing them a fresh allowance of the slower limit.
        """
        expired = [
            key
            for key, (_, started, window_seconds) in self._counters.items()
            if now - started >= window_seconds
        ]
        for key in expired:
            del self._counters[key]

    def reset(self) -> None:
        """Drop all counters. Intended for tests."""
        with self._lock:
            self._counters.clear()


rate_limiter = InMemoryRateLimiter()
