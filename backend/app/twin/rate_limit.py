"""Simple in-memory rate limiter for control API."""

import time
from collections import defaultdict


class RateLimiter:
    """Simple token bucket rate limiter per IP."""

    def __init__(self, max_requests: int, window_seconds: int):
        """
        Initialize rate limiter.

        Args:
            max_requests: Max requests allowed per window
            window_seconds: Window size in seconds
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: defaultdict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        """
        Check if a request from this key is allowed.

        Args:
            key: Identifier (usually IP address)

        Returns:
            True if allowed, False if rate limited
        """
        now = time.time()
        window_start = now - self.window_seconds

        # Remove old requests
        self.requests[key] = [t for t in self.requests[key] if t > window_start]

        if len(self.requests[key]) < self.max_requests:
            self.requests[key].append(now)
            return True

        return False


# Global rate limiters
universe_creation_limiter = RateLimiter(
    max_requests=10,  # 10 per hour
    window_seconds=3600,
)
