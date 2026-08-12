"""Bounded in-memory rate limits for local authentication endpoints."""

from asyncio import Lock
from collections.abc import Callable
from dataclasses import dataclass
from hmac import digest as hmac_digest
from ipaddress import ip_address
from math import ceil
from secrets import token_bytes
from time import monotonic

from backend.services.auth.security import normalize_email


REGISTER_LIMIT = 5
REGISTER_WINDOW_SECONDS = 60 * 60
LOGIN_EMAIL_LIMIT = 5
LOGIN_IP_LIMIT = 30
LOGIN_WINDOW_SECONDS = 15 * 60
DEFAULT_MAX_KEYS = 10_000

_INVALID_EMAIL = b"<invalid-email>"
_INVALID_IP = b"<invalid-ip>"
_PEPPER_BYTES = 32


class RateLimitExceeded(Exception):
    """A fixed public-safe signal carrying only an integer retry delay."""

    __slots__ = ("retry_after",)

    def __init__(self, retry_after: int) -> None:
        self.retry_after = max(1, int(retry_after))
        super().__init__("authentication rate limit exceeded")


@dataclass(slots=True)
class _Window:
    count: int
    expires_at: float


class AuthRateLimiter:
    """Single-process fixed-window limits with bounded, pseudonymous keys.

    One instance is intended to live for the application lifetime. Raw email
    addresses and IP addresses are normalized only long enough to derive an
    HMAC key using a fresh process-local pepper; they are never retained.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = monotonic,
        max_keys: int = DEFAULT_MAX_KEYS,
    ) -> None:
        if isinstance(max_keys, bool) or not isinstance(max_keys, int) or max_keys < 1:
            raise ValueError("max_keys must be a positive integer")
        self._clock = clock
        self._max_keys = max_keys
        self._pepper = token_bytes(_PEPPER_BYTES)
        self._lock = Lock()
        self._registration: dict[bytes, _Window] = {}
        self._login_email: dict[bytes, _Window] = {}
        self._login_ip: dict[bytes, _Window] = {}

    async def check_registration(self, *, client_ip: object) -> None:
        """Consume one registration attempt for a normalized client IP."""
        key = self._key(b"registration-ip", _ip_material(client_ip))
        async with self._lock:
            now = self._clock()
            self._discard_expired(now)
            window = self._registration.get(key)
            if window is not None and window.count >= REGISTER_LIMIT:
                raise RateLimitExceeded(_retry_after(window.expires_at, now))
            self._ensure_capacity(1 if window is None else 0, now)
            self._consume(
                self._registration,
                key,
                window,
                now=now,
                window_seconds=REGISTER_WINDOW_SECONDS,
            )

    async def check_login(self, *, email: object, client_ip: object) -> None:
        """Atomically consume the independent email and IP login buckets."""
        email_key = self._key(b"login-email", _email_material(email))
        ip_key = self._key(b"login-ip", _ip_material(client_ip))
        async with self._lock:
            now = self._clock()
            self._discard_expired(now)
            email_window = self._login_email.get(email_key)
            ip_window = self._login_ip.get(ip_key)

            blocked_until: list[float] = []
            if email_window is not None and email_window.count >= LOGIN_EMAIL_LIMIT:
                blocked_until.append(email_window.expires_at)
            if ip_window is not None and ip_window.count >= LOGIN_IP_LIMIT:
                blocked_until.append(ip_window.expires_at)
            if blocked_until:
                raise RateLimitExceeded(
                    max(_retry_after(expiry, now) for expiry in blocked_until)
                )

            new_keys = int(email_window is None) + int(ip_window is None)
            self._ensure_capacity(new_keys, now)
            self._consume(
                self._login_email,
                email_key,
                email_window,
                now=now,
                window_seconds=LOGIN_WINDOW_SECONDS,
            )
            self._consume(
                self._login_ip,
                ip_key,
                ip_window,
                now=now,
                window_seconds=LOGIN_WINDOW_SECONDS,
            )

    async def record_login_success(self, *, email: object) -> None:
        """Clear only the successful account's email failure bucket."""
        email_key = self._key(b"login-email", _email_material(email))
        async with self._lock:
            now = self._clock()
            self._discard_expired(now)
            self._login_email.pop(email_key, None)

    def _key(self, namespace: bytes, material: bytes) -> bytes:
        return hmac_digest(self._pepper, namespace + b"\0" + material, "sha256")

    def _consume(
        self,
        table: dict[bytes, _Window],
        key: bytes,
        window: _Window | None,
        *,
        now: float,
        window_seconds: int,
    ) -> None:
        if window is None:
            table[key] = _Window(count=1, expires_at=now + window_seconds)
            return
        window.count += 1

    def _ensure_capacity(self, new_keys: int, now: float) -> None:
        if new_keys <= 0:
            return
        current = self._key_count()
        overflow = current + new_keys - self._max_keys
        if overflow <= 0:
            return

        expiries = sorted(
            window.expires_at
            for table in self._tables()
            for window in table.values()
        )
        if overflow <= len(expiries):
            retry_after = _retry_after(expiries[overflow - 1], now)
        else:
            # The configured capacity cannot ever hold this atomic operation.
            retry_after = 1
        raise RateLimitExceeded(retry_after)

    def _discard_expired(self, now: float) -> None:
        for table in self._tables():
            expired = [key for key, window in table.items() if window.expires_at <= now]
            for key in expired:
                del table[key]

    def _key_count(self) -> int:
        return sum(len(table) for table in self._tables())

    def _tables(self) -> tuple[dict[bytes, _Window], ...]:
        return self._registration, self._login_email, self._login_ip


def _email_material(raw_email: object) -> bytes:
    try:
        return normalize_email(raw_email).encode("ascii")
    except Exception:
        return _INVALID_EMAIL


def _ip_material(raw_ip: object) -> bytes:
    if not isinstance(raw_ip, str) or len(raw_ip) > 128:
        return _INVALID_IP
    candidate = raw_ip.strip().partition("%")[0]
    try:
        return ip_address(candidate).compressed.encode("ascii")
    except ValueError:
        return _INVALID_IP


def _retry_after(expires_at: float, now: float) -> int:
    return max(1, ceil(expires_at - now))


__all__ = [
    "AuthRateLimiter",
    "DEFAULT_MAX_KEYS",
    "LOGIN_EMAIL_LIMIT",
    "LOGIN_IP_LIMIT",
    "LOGIN_WINDOW_SECONDS",
    "REGISTER_LIMIT",
    "REGISTER_WINDOW_SECONDS",
    "RateLimitExceeded",
]
