"""Async, context-erasing adapter over the synchronous account repository."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar

from anyio import CapacityLimiter, to_thread
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.db.auth_repository import (
    complete_login as complete_login_record,
    create_user_with_session,
    get_session_by_digest,
    get_user_by_email,
    resolve_active_session,
    revoke_session as revoke_session_record,
)
from backend.db.models import User
from backend.services.auth.types import (
    AuthenticatedUser,
    AuthUnavailable,
    CredentialRecord,
    EmailUnavailable,
    SessionTokenCollision,
)


ResultT = TypeVar("ResultT")


class SQLAlchemyAuthStore:
    """Use independent transactions and the existing bounded DB worker pool."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        limiter: CapacityLimiter | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._limiter = limiter

    async def register_with_session(
        self,
        *,
        email: str,
        password_hash: str,
        token_hash: bytes,
        created_at: datetime,
        expires_at: datetime,
    ) -> AuthenticatedUser:
        return await self._run(
            self._register_with_session,
            email,
            password_hash,
            token_hash,
            created_at,
            expires_at,
        )

    async def get_credentials(self, *, email: str) -> CredentialRecord | None:
        return await self._run(self._get_credentials, email)

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
        return await self._run(
            self._complete_login,
            user_id,
            expected_password_hash,
            updated_password_hash,
            token_hash,
            authenticated_at,
            expires_at,
        )

    async def resolve_session(
        self,
        *,
        token_hash: bytes,
        checked_at: datetime,
        idle_since: datetime,
        touch_before: datetime,
    ) -> AuthenticatedUser | None:
        return await self._run(
            self._resolve_session,
            token_hash,
            checked_at,
            idle_since,
            touch_before,
        )

    async def revoke_session(
        self,
        *,
        token_hash: bytes,
        revoked_at: datetime,
    ) -> None:
        await self._run(self._revoke_session, token_hash, revoked_at)

    async def _run(
        self,
        operation: Callable[..., ResultT],
        *args: object,
    ) -> ResultT:
        result: ResultT | None = None
        error: type[Exception] | None = None
        try:
            result = await to_thread.run_sync(
                operation,
                *args,
                abandon_on_cancel=False,
                limiter=self._limiter,
            )
        except (EmailUnavailable, SessionTokenCollision) as expected:
            error = type(expected)
        except Exception:
            error = AuthUnavailable

        if error is not None:
            raise error() from None
        return result  # type: ignore[return-value]

    def _register_with_session(
        self,
        email: str,
        password_hash: str,
        token_hash: bytes,
        created_at: datetime,
        expires_at: datetime,
    ) -> AuthenticatedUser:
        normalized_created = _to_naive_utc(created_at)
        normalized_expires = _to_naive_utc(expires_at)
        integrity_failed = False
        try:
            with self._session_factory.begin() as session:
                user = create_user_with_session(
                    session,
                    email=email,
                    password_hash=password_hash,
                    token_hash=token_hash,
                    created_at=normalized_created,
                    expires_at=normalized_expires,
                )
                return _to_authenticated_user(user)
        except IntegrityError:
            integrity_failed = True

        if integrity_failed:
            duplicate_email = False
            token_collision = False
            classification_failed = False
            try:
                with self._session_factory() as session:
                    duplicate_email = (
                        get_user_by_email(session, email=email) is not None
                    )
                    token_collision = (
                        get_session_by_digest(session, token_hash=token_hash) is not None
                    )
            except Exception:
                classification_failed = True
            if duplicate_email:
                raise EmailUnavailable() from None
            if token_collision:
                raise SessionTokenCollision() from None
            if classification_failed:
                raise AuthUnavailable() from None
        raise AuthUnavailable() from None

    def _get_credentials(self, email: str) -> CredentialRecord | None:
        with self._session_factory() as session:
            user = get_user_by_email(session, email=email)
            if user is None:
                return None
            return CredentialRecord(
                user_id=user.id,
                email=user.email,
                password_hash=user.password_hash,
                is_active=user.is_active,
            )

    def _complete_login(
        self,
        user_id: int,
        expected_password_hash: str,
        updated_password_hash: str | None,
        token_hash: bytes,
        authenticated_at: datetime,
        expires_at: datetime,
    ) -> AuthenticatedUser | None:
        integrity_failed = False
        try:
            with self._session_factory.begin() as session:
                user = complete_login_record(
                    session,
                    user_id=user_id,
                    expected_password_hash=expected_password_hash,
                    updated_password_hash=updated_password_hash,
                    token_hash=token_hash,
                    authenticated_at=_to_naive_utc(authenticated_at),
                    expires_at=_to_naive_utc(expires_at),
                )
                return None if user is None else _to_authenticated_user(user)
        except IntegrityError:
            integrity_failed = True

        if integrity_failed:
            collision = False
            try:
                with self._session_factory() as session:
                    collision = (
                        get_session_by_digest(session, token_hash=token_hash) is not None
                    )
            except Exception:
                collision = False
            if collision:
                raise SessionTokenCollision() from None
        raise AuthUnavailable() from None

    def _resolve_session(
        self,
        token_hash: bytes,
        checked_at: datetime,
        idle_since: datetime,
        touch_before: datetime,
    ) -> AuthenticatedUser | None:
        with self._session_factory.begin() as session:
            user = resolve_active_session(
                session,
                token_hash=token_hash,
                checked_at=_to_naive_utc(checked_at),
                idle_since=_to_naive_utc(idle_since),
                touch_before=_to_naive_utc(touch_before),
            )
            return None if user is None else _to_authenticated_user(user)

    def _revoke_session(self, token_hash: bytes, revoked_at: datetime) -> None:
        with self._session_factory.begin() as session:
            revoke_session_record(
                session,
                token_hash=token_hash,
                revoked_at=_to_naive_utc(revoked_at),
            )


def _to_authenticated_user(user: User) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user.id,
        email=user.email,
        email_verified=user.email_verified_at is not None,
    )


def _to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("authentication timestamp must be timezone-aware")
    return value.astimezone(UTC).replace(tzinfo=None)
