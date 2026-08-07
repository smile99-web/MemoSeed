"""Tiny in-memory sliding-window rate limiter for sensitive endpoints.

P0 hardening (2026-08-06): the login endpoint had no throttling, so an
attacker could hammer it with unlimited password guesses. This limiter is
intentionally dependency-free and single-process (the backend runs one
uvicorn worker on a 1-core VPS, and the app is per-family — one user, a
couple of devices), so a process-local dict is correct and sufficient.

Design notes:
- Sliding window of timestamps per key, not a fixed bucket, so a burst right
  at a window edge cannot double the allowed rate.
- The lock keeps concurrent async handlers from interleaving appends.
- Bounded growth: idle keys are reaped lazily on access, so a spray of
  distinct emails cannot grow the dict without bound.
"""

from __future__ import annotations

import threading
import time


class SlidingWindowRateLimiter:
    """Allow at most ``max_attempts`` per ``window_seconds`` per key."""

    def __init__(self, max_attempts: int, window_seconds: float) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._since_sweep = 0.0

    def check(self, key: str) -> bool:
        """Record one attempt for ``key``. Returns True if allowed, False if over the limit."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            self._maybe_sweep(cutoff)
            hits = [t for t in self._hits.get(key, []) if t > cutoff]
            if len(hits) >= self.max_attempts:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True

    def _maybe_sweep(self, cutoff: float) -> None:
        """Periodically drop keys with no in-window attempts (bounded memory)."""
        now = time.monotonic()
        if now - self._since_sweep < self.window_seconds:
            return
        self._since_sweep = now
        empty = [k for k, dq in self._hits.items() if not any(t > cutoff for t in dq)]
        for k in empty:
            del self._hits[k]

    def reset(self) -> None:
        """Clear all state (used by tests)."""
        with self._lock:
            self._hits.clear()
