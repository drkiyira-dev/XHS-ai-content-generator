"""Account registration, login and revocable session orchestration."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from anyio import CapacityLimiter, to_thread

from backend.services.auth.security import (
    Argon2idPasswordHasher,
    generate_session_token,
    hash_session_token,
    normalize_email,
    normalize_password,
)
from backend.services.auth.types import (
    AuthenticatedUser,
    AuthStore,
    AuthUnavailable,
    EmailUnavailable,
    InvalidAuthInput,
    InvalidCredentials,
    IssuedSession,
    PasswordHasher,
    SessionTokenCollision,
)


SESSION_ABSOLUTE_TTL = timedelta(days=7)
SESSION_IDLE_TTL = timedelta(hours=24)
SESSION_TOUCH_INTERVAL = timedelta(minutes=5)
SESSION_TOKEN_ATTEMPTS = 3

# A valid Argon2id hash of a non-account constant. It equalizes the expensive
# path for unknown emails and is never accepted as an actual credential.
DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=19456,t=2,p=1$NHjvkomHe8+1VFCBR5w8dg$"
    "8FINW01Z8gwFBg8F3IGc/k8YvqmcTeIEQv0OStoA/Ik"
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AuthService:
    """HTTP-independent authentication service with bounded password work."""

    def __init__(
        self,
        store: AuthStore,
        *,
        password_hasher: PasswordHasher | None = None,
        password_limiter: CapacityLimiter | None = None,
        clock: Callable[[], datetime] = _utc_now,
        token_factory: Callable[[], str] = generate_session_token,
    ) -> None:
        self._store = store
        self._password_hasher = password_hasher or Argon2idPasswordHasher()
        self._password_limiter = password_limiter or CapacityLimiter(2)
        self._clock = clock
        self._token_factory = token_factory

    async def register(self, *, email: object, password: object) -> IssuedSession:
        """Create one free account and automatically sign it in."""
        normalized_email = normalize_email(email)
        normalized_password = normalize_password(password)
        password_hash: str | None = None
        try:
            password_hash = await to_thread.run_sync(
                self._password_hasher.hash,
                normalized_password,
                abandon_on_cancel=False,
                limiter=self._password_limiter,
            )
        except Exception:
            password_hash = None
        finally:
            normalized_password = ""
        if (
            not isinstance(password_hash, str)
            or not password_hash
            or len(password_hash) > 255
        ):
            raise AuthUnavailable() from None

        created_at = _aware_utc(self._clock())
        expires_at = created_at + SESSION_ABSOLUTE_TTL
        for _attempt in range(SESSION_TOKEN_ATTEMPTS):
            raw_token = self._new_token()
            token_hash = hash_session_token(raw_token)
            email_unavailable = False
            try:
                user = await self._store.register_with_session(
                    email=normalized_email,
                    password_hash=password_hash,
                    token_hash=token_hash,
                    created_at=created_at,
                    expires_at=expires_at,
                )
                return IssuedSession(
                    user=user,
                    token=raw_token,
                    expires_at=expires_at,
                )
            except SessionTokenCollision:
                raw_token = ""
                token_hash = b""
                continue
            except EmailUnavailable:
                email_unavailable = True
            if email_unavailable:
                raw_token = ""
                token_hash = b""
                raise EmailUnavailable() from None
        raise AuthUnavailable() from None

    async def login(self, *, email: object, password: object) -> IssuedSession:
        """Use one indistinguishable verify path for unknown or inactive users."""
        email_is_valid = True
        try:
            normalized_email = normalize_email(email)
        except InvalidAuthInput:
            email_is_valid = False
            normalized_email = "invalid@example.invalid"

        try:
            normalized_password = normalize_password(password)
        except InvalidAuthInput:
            normalized_password = "x" * 15
            password_is_valid = False
        else:
            password_is_valid = True

        credentials = await self._store.get_credentials(email=normalized_email)
        encoded_hash = (
            DUMMY_PASSWORD_HASH if credentials is None else credentials.password_hash
        )
        verified = False
        updated_hash: str | None = None
        try:
            verified, updated_hash = await to_thread.run_sync(
                self._safe_verify,
                normalized_password,
                encoded_hash,
                abandon_on_cancel=False,
                limiter=self._password_limiter,
            )
        finally:
            normalized_password = ""
            encoded_hash = ""

        if (
            not email_is_valid
            or not password_is_valid
            or credentials is None
            or not credentials.is_active
            or not verified
        ):
            raise InvalidCredentials() from None
        if updated_hash is not None and (
            not isinstance(updated_hash, str)
            or not updated_hash
            or len(updated_hash) > 255
        ):
            raise AuthUnavailable() from None

        authenticated_at = _aware_utc(self._clock())
        expires_at = authenticated_at + SESSION_ABSOLUTE_TTL
        for _attempt in range(SESSION_TOKEN_ATTEMPTS):
            raw_token = self._new_token()
            token_hash = hash_session_token(raw_token)
            try:
                user = await self._store.complete_login(
                    user_id=credentials.user_id,
                    expected_password_hash=credentials.password_hash,
                    updated_password_hash=updated_hash,
                    token_hash=token_hash,
                    authenticated_at=authenticated_at,
                    expires_at=expires_at,
                )
            except SessionTokenCollision:
                raw_token = ""
                token_hash = b""
                continue
            if user is None:
                raise InvalidCredentials() from None
            return IssuedSession(user=user, token=raw_token, expires_at=expires_at)
        raise AuthUnavailable() from None

    async def get_current_user(
        self,
        raw_token: object,
    ) -> AuthenticatedUser | None:
        """Return the user for one active token without disclosing failure causes."""
        try:
            token_hash = hash_session_token(raw_token)
        except InvalidAuthInput:
            return None
        checked_at = _aware_utc(self._clock())
        return await self._store.resolve_session(
            token_hash=token_hash,
            checked_at=checked_at,
            idle_since=checked_at - SESSION_IDLE_TTL,
            touch_before=checked_at - SESSION_TOUCH_INTERVAL,
        )

    async def logout(self, raw_token: object) -> None:
        """Revoke a well-formed token; malformed and unknown tokens are no-ops."""
        try:
            token_hash = hash_session_token(raw_token)
        except InvalidAuthInput:
            return
        await self._store.revoke_session(
            token_hash=token_hash,
            revoked_at=_aware_utc(self._clock()),
        )

    def _safe_verify(self, password: str, password_hash: str) -> tuple[bool, str | None]:
        try:
            result = self._password_hasher.verify_and_update(password, password_hash)
        except Exception:
            return False, None
        if (
            not isinstance(result, tuple)
            or len(result) != 2
            or not isinstance(result[0], bool)
            or (result[1] is not None and not isinstance(result[1], str))
        ):
            return False, None
        return result

    def _new_token(self) -> str:
        raw_token: str | None = None
        failed = False
        try:
            raw_token = self._token_factory()
            hash_session_token(raw_token)
        except Exception:
            failed = True

        if failed or raw_token is None:
            raw_token = None
            raise AuthUnavailable() from None
        return raw_token


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AuthUnavailable() from None
    return value.astimezone(UTC)
