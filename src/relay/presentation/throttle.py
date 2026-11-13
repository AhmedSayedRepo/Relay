"""Slows password guessing on sign-in. Kept in memory: Relay runs one backend
process, and a restart only resets the counters."""

import time
from collections import deque

WINDOW_SECONDS = 15 * 60
# Failed sign-ins allowed per window before further tries are refused.
PER_EMAIL_AND_CLIENT = 5
PER_CLIENT = 30
PER_EMAIL = 50  # backstop against guessing one account from many addresses
MAX_KEYS = 10_000


class LoginThrottle:
    def __init__(self) -> None:
        self._failures: dict[str, deque[float]] = {}

    def _keys(self, email: str, client: str) -> list[tuple[str, int]]:
        email = email.strip().lower()[:254]
        return [
            (f"pair:{client}:{email}", PER_EMAIL_AND_CLIENT),
            (f"client:{client}", PER_CLIENT),
            (f"email:{email}", PER_EMAIL),
        ]

    def _recent(self, key: str, now: float) -> int:
        times = self._failures.get(key)
        if times is None:
            return 0
        while times and now - times[0] > WINDOW_SECONDS:
            times.popleft()
        if not times:
            del self._failures[key]
            return 0
        return len(times)

    def retry_after(self, email: str, client: str) -> int:
        """Seconds to wait before another try, or 0 when one is allowed."""
        now = time.monotonic()
        wait = 0.0
        for key, limit in self._keys(email, client):
            if self._recent(key, now) >= limit:
                oldest = self._failures[key][0]
                wait = max(wait, WINDOW_SECONDS - (now - oldest))
        return int(wait) + 1 if wait > 0 else 0

    def failed(self, email: str, client: str) -> None:
        now = time.monotonic()
        if len(self._failures) >= MAX_KEYS:
            for key in list(self._failures):
                self._recent(key, now)
            if len(self._failures) >= MAX_KEYS:
                self._failures.clear()  # under flood: start over, never grow
        for key, _ in self._keys(email, client):
            self._failures.setdefault(key, deque()).append(now)

    def succeeded(self, email: str, client: str) -> None:
        self._failures.pop(self._keys(email, client)[0][0], None)
