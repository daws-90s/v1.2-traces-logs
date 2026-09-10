"""In-process rate limiting for the two manual-trigger entrypoints
(POST /investigate, POST /ask, app/main.py) — both kick off a real LLM
call and a Slack post per request. app/main.py's own RCA_MANUAL_TRIGGER_TOKEN
guards *who* can call them; this guards *how often*, so a caller who has
(or guesses) that token still can't spam the Anthropic bill or flood the
Slack channel from one client.

Fixed-size sliding window, keyed per caller (client host, see
app/main.py's _enforce_rate_limit), in-process dict only — correct for a
single-container POC; a multi-replica deployment would need a shared
store (Redis) instead of this, out of scope here.
"""
import time
from collections import defaultdict, deque


class RateLimitExceeded(Exception):
    def __init__(self, retry_after_seconds: float):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"rate limit exceeded, retry after {retry_after_seconds:.0f}s")


class SlidingWindowRateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> None:
        """Raises RateLimitExceeded if `key` is already at its limit for
        the current window; otherwise records this call and returns."""
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()
        if len(hits) >= self.max_requests:
            retry_after = self.window_seconds - (now - hits[0])
            raise RateLimitExceeded(max(retry_after, 0.0))
        hits.append(now)
