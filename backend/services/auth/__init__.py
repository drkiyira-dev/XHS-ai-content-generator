"""Local account authentication core without an HTTP dependency."""

from backend.services.auth.security import (
    Argon2idPasswordHasher,
    generate_session_token,
    hash_session_token,
    normalize_email,
    normalize_password,
)
from backend.services.auth.rate_limit import AuthRateLimiter, RateLimitExceeded
from backend.services.auth.service import AuthService
from backend.services.auth.sqlalchemy import SQLAlchemyAuthStore
from backend.services.auth.types import (
    AuthenticatedUser,
    AuthenticationService,
    AuthError,
    AuthStore,
    AuthUnavailable,
    CredentialRecord,
    EmailUnavailable,
    InvalidAuthInput,
    InvalidCredentials,
    IssuedSession,
    PasswordHasher,
    SessionTokenCollision,
)


__all__ = [
    "Argon2idPasswordHasher",
    "AuthenticatedUser",
    "AuthenticationService",
    "AuthError",
    "AuthRateLimiter",
    "AuthStore",
    "AuthService",
    "AuthUnavailable",
    "CredentialRecord",
    "EmailUnavailable",
    "InvalidAuthInput",
    "InvalidCredentials",
    "IssuedSession",
    "PasswordHasher",
    "RateLimitExceeded",
    "SessionTokenCollision",
    "SQLAlchemyAuthStore",
    "generate_session_token",
    "hash_session_token",
    "normalize_email",
    "normalize_password",
]
