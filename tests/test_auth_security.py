"""Security contracts for email, password and session-token primitives."""

from hashlib import sha256
from datetime import UTC, datetime

import pytest

from backend.services.auth import (
    Argon2idPasswordHasher,
    AuthenticatedUser,
    CredentialRecord,
    InvalidAuthInput,
    IssuedSession,
    generate_session_token,
    hash_session_token,
    normalize_email,
    normalize_password,
)
from backend.services.auth.service import DUMMY_PASSWORD_HASH


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Demo.User+tag@Example.COM  ", "demo.user+tag@example.com"),
        ("person@bücher.example", "person@xn--bcher-kva.example"),
    ],
)
def test_email_is_normalized_offline_to_lowercase_ascii(
    raw: str,
    expected: str,
) -> None:
    normalized = normalize_email(raw)

    assert normalized == expected
    assert normalize_email(normalized) == normalized
    assert normalized.isascii()


def test_email_accepts_254_ascii_bytes_and_rejects_255() -> None:
    maximum = "a" * 64 + "@" + "b" * 63 + "." + "c" * 63 + "." + "d" * 61
    oversized = maximum + "d"

    assert len(maximum.encode("ascii")) == 254
    assert normalize_email(maximum) == maximum
    with pytest.raises(InvalidAuthInput):
        normalize_email(oversized)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "not-an-email",
        "Name <demo@example.com>",
        "用户@example.com",
        "demo@example.com\nBcc:private@example.com",
        "a" * 1_025 + "@example.com",
    ],
)
def test_invalid_or_unsafe_email_is_rejected_without_echo(raw: object) -> None:
    with pytest.raises(InvalidAuthInput) as caught:
        normalize_email(raw)

    assert repr(raw) not in str(caught.value)


def test_password_uses_nfc_but_preserves_spaces_and_case() -> None:
    decomposed = " Cafe\u0301 secure words "
    normalized = normalize_password(decomposed)

    assert normalized == " Café secure words "
    assert normalized.startswith(" ")
    assert normalized.endswith(" ")
    assert normalize_password("Case Sensitive Password") != normalize_password(
        "case sensitive password"
    )


@pytest.mark.parametrize(
    ("length", "accepted"),
    [(14, False), (15, True), (128, True), (129, False)],
)
def test_password_length_boundaries_count_unicode_codepoints(
    length: int,
    accepted: bool,
) -> None:
    password = "密" * length
    if accepted:
        assert normalize_password(password) == password
    else:
        with pytest.raises(InvalidAuthInput):
            normalize_password(password)


def test_oversized_password_is_rejected_before_unicode_normalization() -> None:
    with pytest.raises(InvalidAuthInput):
        normalize_password("e\u0301" * 257)


def test_unpaired_unicode_surrogate_is_rejected_as_invalid_input() -> None:
    with pytest.raises(InvalidAuthInput) as caught:
        normalize_password("a" * 14 + "\ud800")

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_real_argon2id_hashes_are_salted_and_use_frozen_minimum_parameters() -> None:
    hasher = Argon2idPasswordHasher()
    password = "correct horse battery staple"

    first = hasher.hash(password)
    second = hasher.hash(password)

    assert first != second
    assert first.startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    assert len(first) <= 255
    assert hasher.verify_and_update(password, first) == (True, None)
    assert hasher.verify_and_update("incorrect password value", first)[0] is False


def test_dummy_hash_is_valid_argon2id_and_never_accepts_the_test_password() -> None:
    hasher = Argon2idPasswordHasher()

    assert DUMMY_PASSWORD_HASH.startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    assert hasher.verify_and_update("unknown account password", DUMMY_PASSWORD_HASH) == (
        False,
        None,
    )


def test_session_token_is_256_bit_base64url_and_only_digest_is_stable() -> None:
    first = generate_session_token()
    second = generate_session_token()

    assert first != second
    assert len(first) == len(second) == 43
    assert first.replace("-", "").replace("_", "").isalnum()
    assert hash_session_token(first) == sha256(first.encode("ascii")).digest()
    assert len(hash_session_token(first)) == 32


@pytest.mark.parametrize("raw", [None, "", "A" * 42, "A" * 44, "+" * 43])
def test_malformed_session_token_is_rejected_without_echo(raw: object) -> None:
    with pytest.raises(InvalidAuthInput) as caught:
        hash_session_token(raw)

    assert repr(raw) not in str(caught.value)


def test_private_hash_and_raw_session_token_are_hidden_from_dataclass_repr() -> None:
    private_hash = "private-password-hash"
    raw_token = "A" * 43
    user = AuthenticatedUser(1, "demo@example.com", False)
    credentials = CredentialRecord(1, user.email, private_hash, True)
    issued = IssuedSession(user, raw_token, datetime.now(UTC))

    assert private_hash not in repr(credentials)
    assert raw_token not in repr(issued)
