"""Deterministic security tests for the in-memory authentication limiter."""

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from backend.services.auth.rate_limit import (
    AuthRateLimiter,
    LOGIN_EMAIL_LIMIT,
    LOGIN_IP_LIMIT,
    LOGIN_WINDOW_SECONDS,
    REGISTER_LIMIT,
    REGISTER_WINDOW_SECONDS,
    RateLimitExceeded,
)


class MutableMonotonic:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_registration_allows_five_attempts_then_resets_at_one_hour() -> None:
    clock = MutableMonotonic()
    limiter = AuthRateLimiter(clock=clock)

    async def scenario() -> None:
        for _ in range(REGISTER_LIMIT):
            await limiter.check_registration(client_ip="203.0.113.10")

        with pytest.raises(RateLimitExceeded) as caught:
            await limiter.check_registration(client_ip="203.0.113.10")
        assert caught.value.retry_after == REGISTER_WINDOW_SECONDS
        assert isinstance(caught.value.retry_after, int)

        clock.advance(REGISTER_WINDOW_SECONDS - 0.25)
        with pytest.raises(RateLimitExceeded) as almost_expired:
            await limiter.check_registration(client_ip="203.0.113.10")
        assert almost_expired.value.retry_after == 1

        clock.advance(0.25)
        await limiter.check_registration(client_ip="203.0.113.10")

    asyncio.run(scenario())


def test_login_email_bucket_normalizes_case_and_is_independent_of_ip() -> None:
    limiter = AuthRateLimiter(clock=MutableMonotonic())

    async def scenario() -> None:
        for index in range(LOGIN_EMAIL_LIMIT):
            await limiter.check_login(
                email=" Demo@Example.COM " if index % 2 else "demo@example.com",
                client_ip=f"198.51.100.{index + 1}",
            )

        with pytest.raises(RateLimitExceeded) as caught:
            await limiter.check_login(
                email="DEMO@example.com",
                client_ip="198.51.100.200",
            )

        assert caught.value.retry_after == LOGIN_WINDOW_SECONDS

    asyncio.run(scenario())


def test_invalid_emails_share_one_bounded_failure_bucket() -> None:
    limiter = AuthRateLimiter(clock=MutableMonotonic())

    async def scenario() -> None:
        invalid_emails: list[object] = [None, "", "bad", "also bad", "x@"]
        for index, email in enumerate(invalid_emails):
            await limiter.check_login(
                email=email,
                client_ip=f"192.0.2.{index + 1}",
            )

        with pytest.raises(RateLimitExceeded):
            await limiter.check_login(
                email="not-an-address",
                client_ip="192.0.2.200",
            )

    asyncio.run(scenario())


def test_login_ip_bucket_allows_thirty_distinct_accounts() -> None:
    limiter = AuthRateLimiter(clock=MutableMonotonic())

    async def scenario() -> None:
        for index in range(LOGIN_IP_LIMIT):
            await limiter.check_login(
                email=f"person{index}@example.com",
                client_ip="2001:db8:0:0:0:0:0:20" if index % 2 else "2001:db8::20",
            )

        with pytest.raises(RateLimitExceeded) as caught:
            await limiter.check_login(
                email="next@example.com",
                client_ip="2001:db8::20",
            )

        assert caught.value.retry_after == LOGIN_WINDOW_SECONDS

    asyncio.run(scenario())


def test_success_clears_email_bucket_but_preserves_ip_bucket() -> None:
    limiter = AuthRateLimiter(clock=MutableMonotonic())

    async def scenario() -> None:
        for _ in range(LOGIN_EMAIL_LIMIT):
            await limiter.check_login(
                email="demo@example.com",
                client_ip="203.0.113.50",
            )
        await limiter.record_login_success(email=" DEMO@EXAMPLE.COM ")

        # The email can be attempted again from another address.
        await limiter.check_login(
            email="demo@example.com",
            client_ip="203.0.113.51",
        )

        # The original IP still owns all five earlier attempts.
        for index in range(LOGIN_IP_LIMIT - LOGIN_EMAIL_LIMIT):
            await limiter.check_login(
                email=f"fresh{index}@example.com",
                client_ip="203.0.113.50",
            )
        with pytest.raises(RateLimitExceeded):
            await limiter.check_login(
                email="blocked@example.com",
                client_ip="203.0.113.50",
            )

    asyncio.run(scenario())


def test_capacity_exhaustion_fails_closed_until_keys_expire() -> None:
    clock = MutableMonotonic()
    limiter = AuthRateLimiter(clock=clock, max_keys=2)

    async def scenario() -> None:
        await limiter.check_login(
            email="demo@example.com",
            client_ip="198.51.100.10",
        )

        with pytest.raises(RateLimitExceeded) as caught:
            await limiter.check_registration(client_ip="198.51.100.11")
        assert caught.value.retry_after == LOGIN_WINDOW_SECONDS

        clock.advance(LOGIN_WINDOW_SECONDS)
        await limiter.check_registration(client_ip="198.51.100.11")

    asyncio.run(scenario())


def test_capacity_rejects_an_atomic_login_that_can_never_fit() -> None:
    limiter = AuthRateLimiter(clock=MutableMonotonic(), max_keys=1)

    async def scenario() -> None:
        with pytest.raises(RateLimitExceeded) as caught:
            await limiter.check_login(
                email="demo@example.com",
                client_ip="198.51.100.20",
            )
        assert caught.value.retry_after == 1
        assert limiter._key_count() == 0

    asyncio.run(scenario())


def test_only_hmac_digests_are_retained_and_peppers_are_per_instance() -> None:
    raw_email = "private.person@example.com"
    raw_ip = "203.0.113.77"
    first = AuthRateLimiter(clock=MutableMonotonic())
    second = AuthRateLimiter(clock=MutableMonotonic())

    async def scenario() -> None:
        await first.check_login(email=raw_email, client_ip=raw_ip)
        await second.check_login(email=raw_email, client_ip=raw_ip)

    asyncio.run(scenario())

    first_keys = tuple(key for table in first._tables() for key in table)
    second_keys = tuple(key for table in second._tables() for key in table)
    assert first_keys and second_keys
    assert all(isinstance(key, bytes) and len(key) == 32 for key in first_keys)
    assert set(first_keys).isdisjoint(second_keys)
    assert raw_email not in repr(first._tables())
    assert raw_ip not in repr(first._tables())
    assert raw_email not in repr(first)
    assert raw_ip not in repr(first)


def test_exception_is_fixed_and_never_contains_the_limited_identity() -> None:
    private_identity = "private.person@example.com"
    limiter = AuthRateLimiter(clock=MutableMonotonic())

    async def scenario() -> RateLimitExceeded:
        for index in range(LOGIN_EMAIL_LIMIT):
            await limiter.check_login(
                email=private_identity,
                client_ip=f"192.0.2.{index + 1}",
            )
        with pytest.raises(RateLimitExceeded) as caught:
            await limiter.check_login(
                email=private_identity,
                client_ip="192.0.2.99",
            )
        return caught.value

    error = asyncio.run(scenario())
    assert str(error) == "authentication rate limit exceeded"
    assert private_identity not in str(error)
    assert private_identity not in repr(error)
    assert error.__cause__ is None


@pytest.mark.parametrize(
    ("operation", "expected_allowed"),
    [
        ("registration", REGISTER_LIMIT),
        ("login_email", LOGIN_EMAIL_LIMIT),
        ("login_ip", LOGIN_IP_LIMIT),
    ],
)
def test_concurrent_attempts_never_cross_a_threshold(
    operation: str,
    expected_allowed: int,
) -> None:
    limiter = AuthRateLimiter(clock=MutableMonotonic())

    async def attempt(index: int) -> bool:
        call: Callable[[], Awaitable[None]]
        if operation == "registration":
            call = lambda: limiter.check_registration(client_ip="203.0.113.90")
        elif operation == "login_email":
            call = lambda: limiter.check_login(
                email="same@example.com",
                client_ip=f"198.51.100.{index + 1}",
            )
        else:
            call = lambda: limiter.check_login(
                email=f"person{index}@example.com",
                client_ip="203.0.113.91",
            )
        try:
            await call()
        except RateLimitExceeded:
            return False
        return True

    async def scenario() -> list[bool]:
        attempts = max(expected_allowed + 10, 40)
        return list(await asyncio.gather(*(attempt(index) for index in range(attempts))))

    results = asyncio.run(scenario())
    assert sum(results) == expected_allowed


@pytest.mark.parametrize("max_keys", [True, 0, -1, 1.5, "10"])
def test_invalid_capacity_is_rejected(max_keys: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        AuthRateLimiter(max_keys=max_keys)  # type: ignore[arg-type]
