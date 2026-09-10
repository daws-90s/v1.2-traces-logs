import pytest

from app.security.ratelimit import RateLimitExceeded, SlidingWindowRateLimiter


def test_allows_up_to_the_limit():
    limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=60)
    for _ in range(3):
        limiter.check("caller-a")  # must not raise


def test_rejects_once_limit_exceeded():
    limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)
    limiter.check("caller-a")
    limiter.check("caller-a")
    with pytest.raises(RateLimitExceeded):
        limiter.check("caller-a")


def test_callers_are_isolated():
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)
    limiter.check("caller-a")
    limiter.check("caller-b")  # different key, own budget — must not raise


def test_window_slides_out_old_hits():
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)
    limiter.check("caller-a")
    # Simulate the earlier hit having aged out of the window instead of
    # sleeping in a test.
    limiter._hits["caller-a"][0] -= 61
    limiter.check("caller-a")  # must not raise now that the window slid


def test_retry_after_is_positive_and_bounded_by_window():
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)
    limiter.check("caller-a")
    with pytest.raises(RateLimitExceeded) as exc_info:
        limiter.check("caller-a")
    assert 0 <= exc_info.value.retry_after_seconds <= 60
