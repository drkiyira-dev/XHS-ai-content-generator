"""Content-free contracts for local account authentication."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


class AuthError(Exception):
    """Base class whose subclasses expose only fixed, non-sensitive details."""


class InvalidAuthInput(AuthError):
    """Registration input does not satisfy the frozen account rules."""

    def __init__(self) -> None:
        super().__init__("invalid authentication input")


class EmailUnavailable(AuthError):
    """A normalized email cannot be used for a new account."""

    def __init__(self) -> None:
        super().__init__("email is unavailable")


class InvalidCredentials(AuthError):
    """Login credentials do not identify an active account."""

    def __init__(self) -> None:
        super().__init__("invalid credentials")


class AuthUnavailable(AuthError):
    """Authentication storage or cryptographic work could not complete."""

    def __init__(self) -> None:
        super().__init__("authentication is unavailable")


class SessionTokenCollision(AuthUnavailable):
    """Internal retry signal for an astronomically unlikely digest collision."""

    def __init__(self) -> None:
        super().__init__()


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Authenticated account data safe for a later public response."""

    user_id: int
    email: str
    email_verified: bool


@dataclass(frozen=True, slots=True)
class CredentialRecord:
    """Private login snapshot whose password hash is hidden from repr."""

    user_id: int
    email: str
    password_hash: str = field(repr=False)
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """One raw token returned once to the future HTTP cookie layer."""

    user: AuthenticatedUser
    token: str = field(repr=False)
    expires_at: datetime


class AuthStore(Protocol):
    """Async account persistence boundary implemented over SQLAlchemy."""

    async def register_with_session(
        self,
        *,
        email: str,
        password_hash: str,
        token_hash: bytes,
        created_at: datetime,
        expires_at: datetime,
    ) -> AuthenticatedUser:
        """Atomically create an account and its first login session."""
        ...

    async def get_credentials(self, *, email: str) -> CredentialRecord | None:
        """Return a private credential snapshot for one normalized email."""
        ...

    async def complete_login(
        self,
        *,
        user_id: int,
        expected_password_hash: str,
        updated_password_hash: str | None,
        token_hash: bytes,
        authenticated_at: datetime,
        expires_at: datetime,
    ) -> AuthenticatedUser | None:
        """Conditionally update the user and create a session in one transaction."""
        ...

    async def resolve_session(
        self,
        *,
        token_hash: bytes,
        checked_at: datetime,
        idle_since: datetime,
        touch_before: datetime,
    ) -> AuthenticatedUser | None:
        """Resolve and optionally touch one active session."""
        ...

    async def revoke_session(
        self,
        *,
        token_hash: bytes,
        revoked_at: datetime,
    ) -> None:
        """Idempotently revoke a current session without disclosing its existence."""
        ...


class PasswordHasher(Protocol):
    """Injectable synchronous password primitive run in a bounded worker."""

    def hash(self, password: str) -> str:
        """Return a salted adaptive hash."""
        ...

    def verify_and_update(
        self,
        password: str,
        password_hash: str,
    ) -> tuple[bool, str | None]:
        """Verify and optionally return a hash using the current work factors."""
        ...


class AuthenticationService(Protocol):
    """Async boundary consumed by the later FastAPI authentication routes."""

    async def register(self, *, email: object, password: object) -> IssuedSession:
        """Create one account and its initial session."""
        ...

    async def login(self, *, email: object, password: object) -> IssuedSession:
        """Authenticate credentials and issue a fresh session."""
        ...

    async def get_current_user(
        self,
        raw_token: object,
    ) -> AuthenticatedUser | None:
        """Resolve one active session without accepting a caller-supplied user ID."""
        ...

    async def logout(self, raw_token: object) -> None:
        """Idempotently revoke one well-formed session token."""
        ...
