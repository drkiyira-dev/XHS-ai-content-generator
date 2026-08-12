"""Synchronous account repository whose caller owns each transaction."""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.db.models import AuthSession, User


def create_user_with_session(
    session: Session,
    *,
    email: str,
    password_hash: str,
    token_hash: bytes,
    created_at: datetime,
    expires_at: datetime,
) -> User:
    """Insert a user and first session atomically in the caller's transaction."""
    user = User(
        email=email,
        password_hash=password_hash,
        is_active=True,
        email_verified_at=None,
        created_at=created_at,
        updated_at=created_at,
        last_login_at=created_at,
    )
    session.add(user)
    session.flush()
    session.add(
        AuthSession(
            user_id=user.id,
            token_hash=token_hash,
            created_at=created_at,
            last_seen_at=created_at,
            expires_at=expires_at,
            revoked_at=None,
        )
    )
    session.flush()
    return user


def get_user_by_email(session: Session, *, email: str) -> User | None:
    """Return one account by its already normalized address."""
    return session.execute(select(User).where(User.email == email)).scalar_one_or_none()


def get_session_by_digest(
    session: Session,
    *,
    token_hash: bytes,
) -> AuthSession | None:
    """Return one session solely for classifying a unique-key collision."""
    return session.execute(
        select(AuthSession).where(AuthSession.token_hash == token_hash)
    ).scalar_one_or_none()


def complete_login(
    session: Session,
    *,
    user_id: int,
    expected_password_hash: str,
    updated_password_hash: str | None,
    token_hash: bytes,
    authenticated_at: datetime,
    expires_at: datetime,
) -> User | None:
    """Prevent password or active-state changes racing a successful verify."""
    values: dict[str, object] = {
        "last_login_at": authenticated_at,
        "updated_at": authenticated_at,
    }
    if updated_password_hash is not None:
        values["password_hash"] = updated_password_hash

    result = session.execute(
        update(User)
        .where(
            User.id == user_id,
            User.is_active.is_(True),
            User.password_hash == expected_password_hash,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return None

    session.add(
        AuthSession(
            user_id=user_id,
            token_hash=token_hash,
            created_at=authenticated_at,
            last_seen_at=authenticated_at,
            expires_at=expires_at,
            revoked_at=None,
        )
    )
    session.flush()
    return session.execute(
        select(User)
        .where(User.id == user_id)
        .execution_options(populate_existing=True)
    ).scalar_one()


def resolve_active_session(
    session: Session,
    *,
    token_hash: bytes,
    checked_at: datetime,
    idle_since: datetime,
    touch_before: datetime,
) -> User | None:
    """Resolve and rate-limit touching an active, non-idle session."""
    result = _select_active_session(
        session,
        token_hash=token_hash,
        checked_at=checked_at,
        idle_since=idle_since,
    )
    if result is None:
        return None

    user, login_session = result
    observed_last_seen = login_session.last_seen_at
    if observed_last_seen <= touch_before:
        touched = session.execute(
            update(AuthSession)
            .where(
                AuthSession.id == login_session.id,
                AuthSession.last_seen_at == observed_last_seen,
                AuthSession.last_seen_at <= touch_before,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > checked_at,
                AuthSession.last_seen_at > idle_since,
            )
            .values(last_seen_at=checked_at)
            .execution_options(synchronize_session=False)
        )
        if touched.rowcount != 1:
            refreshed = _select_active_session(
                session,
                token_hash=token_hash,
                checked_at=checked_at,
                idle_since=idle_since,
            )
            return None if refreshed is None else refreshed[0]
    return user


def _select_active_session(
    session: Session,
    *,
    token_hash: bytes,
    checked_at: datetime,
    idle_since: datetime,
) -> tuple[User, AuthSession] | None:
    """Read one valid session without mutating its activity timestamp."""
    row = session.execute(
        select(User, AuthSession)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(
            AuthSession.token_hash == token_hash,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > checked_at,
            AuthSession.last_seen_at > idle_since,
            User.is_active.is_(True),
        )
    ).one_or_none()
    if row is None:
        return None
    return row[0], row[1]


def revoke_session(
    session: Session,
    *,
    token_hash: bytes,
    revoked_at: datetime,
) -> None:
    """Idempotently revoke a digest without revealing whether it existed."""
    session.execute(
        update(AuthSession)
        .where(
            AuthSession.token_hash == token_hash,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=revoked_at)
        .execution_options(synchronize_session=False)
    )
