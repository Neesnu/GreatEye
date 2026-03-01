import time
from collections import defaultdict


class RateLimiter:
    """In-memory rate limiter. Resets on app restart."""

    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        """Check if the key is within the rate limit."""
        now = time.monotonic()
        cutoff = now - self.window_seconds

        # Prune old entries
        self._attempts[key] = [
            t for t in self._attempts[key] if t > cutoff
        ]

        return len(self._attempts[key]) < self.max_attempts

    def record(self, key: str) -> None:
        """Record an attempt for the given key."""
        self._attempts[key].append(time.monotonic())


# 5 login attempts per 60 seconds per IP
login_limiter = RateLimiter(max_attempts=5, window_seconds=60)
