"""Input normalization and cryptographic primitives for local accounts."""

from hashlib import sha256
import re
from secrets import token_urlsafe
import unicodedata

from email_validator import EmailNotValidError, validate_email
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from backend.services.auth.types import InvalidAuthInput


PASSWORD_MIN_LENGTH = 15
PASSWORD_MAX_LENGTH = 128
PASSWORD_RAW_MAX_LENGTH = 512
EMAIL_RAW_MAX_LENGTH = 1_024
SESSION_TOKEN_BYTES = 32
SESSION_TOKEN_LENGTH = 43
SESSION_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


class Argon2idPasswordHasher:
    """pwdlib wrapper with explicit OWASP-minimum Argon2id work factors."""

    def __init__(self) -> None:
        self._password_hash = PasswordHash(
            (
                Argon2Hasher(
                    memory_cost=19_456,
                    time_cost=2,
                    parallelism=1,
                ),
            )
        )

    def hash(self, password: str) -> str:
        return self._password_hash.hash(password)

    def verify_and_update(
        self,
        password: str,
        password_hash: str,
    ) -> tuple[bool, str | None]:
        return self._password_hash.verify_and_update(password, password_hash)


def normalize_email(raw_email: object) -> str:
    """Return one lower-case ASCII address without any network lookup."""
    if not isinstance(raw_email, str) or len(raw_email) > EMAIL_RAW_MAX_LENGTH:
        raise InvalidAuthInput()
    candidate = raw_email.strip()
    if not candidate:
        raise InvalidAuthInput()

    normalized: str | None = None
    try:
        result = validate_email(
            candidate,
            check_deliverability=False,
            allow_smtputf8=False,
            allow_display_name=False,
        )
        normalized = result.ascii_email
    except EmailNotValidError:
        normalized = None

    if normalized is None:
        raise InvalidAuthInput()
    normalized = normalized.lower()
    if not 3 <= len(normalized.encode("ascii")) <= 254:
        raise InvalidAuthInput()
    return normalized


def normalize_password(raw_password: object) -> str:
    """Apply NFC while preserving case and leading or trailing spaces."""
    if not isinstance(raw_password, str) or len(raw_password) > PASSWORD_RAW_MAX_LENGTH:
        raise InvalidAuthInput()
    normalized = unicodedata.normalize("NFC", raw_password)
    is_utf8 = True
    try:
        normalized.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        is_utf8 = False
    if not is_utf8 or not PASSWORD_MIN_LENGTH <= len(normalized) <= PASSWORD_MAX_LENGTH:
        raise InvalidAuthInput()
    return normalized


def generate_session_token() -> str:
    """Generate a 256-bit base64url token suitable for an HttpOnly cookie."""
    token = token_urlsafe(SESSION_TOKEN_BYTES)
    if not SESSION_TOKEN_PATTERN.fullmatch(token):
        raise RuntimeError("session token generation failed")
    return token


def hash_session_token(raw_token: object) -> bytes:
    """Validate and hash a raw token; callers must never persist the input."""
    if not isinstance(raw_token, str) or not SESSION_TOKEN_PATTERN.fullmatch(raw_token):
        raise InvalidAuthInput()
    return sha256(raw_token.encode("ascii")).digest()
