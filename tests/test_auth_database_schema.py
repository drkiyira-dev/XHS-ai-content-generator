"""Database schema contract for demonstration accounts and login sessions."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateTable

from backend.db import AuthSession, Base, User


PASSWORD_HASH = "$argon2id$v=19$m=65536,t=3,p=4$test-only-hash"


@contextmanager
def auth_database(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    """Create an isolated SQLite schema with foreign-key enforcement enabled."""
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'auth-schema.sqlite3'}",
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=Session,
    )
    try:
        yield factory
    finally:
        engine.dispose()


def _create_user(
    session: Session,
    *,
    email: str = "demo@example.com",
    created_at: datetime,
) -> User:
    user = User(
        email=email,
        password_hash=PASSWORD_HASH,
        is_active=True,
        email_verified_at=None,
        created_at=created_at,
        updated_at=created_at,
        last_login_at=None,
    )
    session.add(user)
    session.flush()
    return user


def _create_session(
    session: Session,
    *,
    user_id: int,
    token_seed: bytes = b"session-one",
    created_at: datetime,
) -> AuthSession:
    login_session = AuthSession(
        user_id=user_id,
        token_hash=sha256(token_seed).digest(),
        created_at=created_at,
        last_seen_at=created_at,
        expires_at=created_at + timedelta(days=7),
        revoked_at=None,
    )
    session.add(login_session)
    session.flush()
    return login_session


def test_auth_schema_contains_only_hashed_session_credentials(
    tmp_path: Path,
) -> None:
    with auth_database(tmp_path) as factory:
        engine = factory.kw["bind"]
        schema = inspect(engine)
        user_columns = {column["name"] for column in schema.get_columns("users")}
        session_columns = {
            column["name"] for column in schema.get_columns("auth_sessions")
        }

    assert user_columns == {
        "id",
        "email",
        "password_hash",
        "is_active",
        "email_verified_at",
        "created_at",
        "updated_at",
        "last_login_at",
    }
    assert session_columns == {
        "id",
        "user_id",
        "token_hash",
        "created_at",
        "last_seen_at",
        "expires_at",
        "revoked_at",
    }
    assert "token" not in session_columns
    assert "activation_token" not in user_columns


def test_user_and_session_can_be_persisted_without_fake_email_verification(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 12, 3, 0, 0)
    expected_digest = sha256(b"session-one").digest()

    with auth_database(tmp_path) as factory:
        with factory.begin() as session:
            user = _create_user(session, created_at=now)
            login_session = _create_session(
                session,
                user_id=user.id,
                created_at=now,
            )
            user_id = user.id
            session_id = login_session.id

        with factory() as session:
            stored_user = session.get(User, user_id)
            stored_session = session.get(AuthSession, session_id)

    assert stored_user is not None
    assert stored_user.email == "demo@example.com"
    assert stored_user.email_verified_at is None
    assert stored_user.password_hash == PASSWORD_HASH
    assert stored_session is not None
    assert stored_session.user_id == user_id
    assert stored_session.token_hash == expected_digest
    assert len(stored_session.token_hash) == 32
    assert stored_session.revoked_at is None


def test_normalized_email_is_unique_and_uppercase_is_rejected(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 12, 3, 0, 0)

    with auth_database(tmp_path) as factory:
        with factory.begin() as session:
            _create_user(session, created_at=now)

        with pytest.raises(IntegrityError):
            with factory.begin() as session:
                _create_user(session, created_at=now)

        with pytest.raises(IntegrityError):
            with factory.begin() as session:
                _create_user(
                    session,
                    email="Uppercase@example.com",
                    created_at=now,
                )


def test_empty_password_hash_is_rejected(tmp_path: Path) -> None:
    now = datetime(2026, 8, 12, 3, 0, 0)

    with auth_database(tmp_path) as factory:
        with pytest.raises(IntegrityError):
            with factory.begin() as session:
                session.add(
                    User(
                        email="demo@example.com",
                        password_hash="",
                        is_active=True,
                        email_verified_at=None,
                        created_at=now,
                        updated_at=now,
                        last_login_at=None,
                    )
                )


def test_session_token_digest_must_be_exact_and_unique(tmp_path: Path) -> None:
    now = datetime(2026, 8, 12, 3, 0, 0)

    with auth_database(tmp_path) as factory:
        with factory.begin() as session:
            user = _create_user(session, created_at=now)
            _create_session(session, user_id=user.id, created_at=now)
            user_id = user.id

        with pytest.raises(IntegrityError):
            with factory.begin() as session:
                _create_session(session, user_id=user_id, created_at=now)

        with pytest.raises(IntegrityError):
            with factory.begin() as session:
                session.add(
                    AuthSession(
                        user_id=user_id,
                        token_hash=b"too-short",
                        created_at=now,
                        last_seen_at=now,
                        expires_at=now + timedelta(days=7),
                        revoked_at=None,
                    )
                )


def test_session_requires_an_existing_user_and_user_deletion_is_restricted(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 12, 3, 0, 0)

    with auth_database(tmp_path) as factory:
        with pytest.raises(IntegrityError):
            with factory.begin() as session:
                _create_session(session, user_id=999_999, created_at=now)

        with factory.begin() as session:
            user = _create_user(session, created_at=now)
            _create_session(session, user_id=user.id, created_at=now)
            user_id = user.id

        with pytest.raises(IntegrityError):
            with factory.begin() as session:
                stored_user = session.get(User, user_id)
                assert stored_user is not None
                session.delete(stored_user)


@pytest.mark.parametrize(
    ("last_seen_delta", "expires_delta", "revoked_delta"),
    [
        (timedelta(0), timedelta(0), None),
        (timedelta(seconds=-1), timedelta(days=7), None),
        (timedelta(days=8), timedelta(days=7), None),
        (timedelta(0), timedelta(days=7), timedelta(seconds=-1)),
    ],
)
def test_session_rejects_invalid_timestamp_order(
    tmp_path: Path,
    last_seen_delta: timedelta,
    expires_delta: timedelta,
    revoked_delta: timedelta | None,
) -> None:
    now = datetime(2026, 8, 12, 3, 0, 0)

    with auth_database(tmp_path) as factory:
        with factory.begin() as session:
            user = _create_user(session, created_at=now)
            user_id = user.id

        with pytest.raises(IntegrityError):
            with factory.begin() as session:
                session.add(
                    AuthSession(
                        user_id=user_id,
                        token_hash=sha256(repr(last_seen_delta).encode()).digest(),
                        created_at=now,
                        last_seen_at=now + last_seen_delta,
                        expires_at=now + expires_delta,
                        revoked_at=(
                            None if revoked_delta is None else now + revoked_delta
                        ),
                    )
                )


def test_mysql_orm_types_match_the_auth_migration_contract() -> None:
    users_sql = str(CreateTable(User.__table__).compile(dialect=mysql.dialect()))
    sessions_sql = str(
        CreateTable(AuthSession.__table__).compile(dialect=mysql.dialect())
    )

    assert users_sql.count("BIGINT UNSIGNED") == 1
    assert (
        "VARCHAR(254) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin"
        in users_sql
    )
    assert "VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin" in users_sql
    assert users_sql.count("DATETIME(6)") == 4
    assert "DEFAULT 1" in users_sql
    assert "ENGINE=InnoDB" in users_sql
    assert "CHARSET=utf8mb4" in users_sql
    assert "COLLATE utf8mb4_0900_ai_ci" in users_sql
    assert sessions_sql.count("BIGINT UNSIGNED") == 2
    assert "VARBINARY(32)" in sessions_sql
    assert sessions_sql.count("DATETIME(6)") == 4
    assert "ON DELETE RESTRICT ON UPDATE RESTRICT" in sessions_sql
    assert "ENGINE=InnoDB" in sessions_sql
    assert "CHARSET=utf8mb4" in sessions_sql
    assert "COLLATE utf8mb4_0900_ai_ci" in sessions_sql


def test_auth_migration_is_scoped_and_non_destructive() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "003_auth_tables.sql"
    ).read_text(encoding="utf-8")
    executable_sql = "\n".join(
        line for line in migration.splitlines() if not line.lstrip().startswith("--")
    )
    statements = [
        statement.strip() for statement in executable_sql.split(";") if statement.strip()
    ]
    normalized = [" ".join(statement.upper().split()) for statement in statements]

    assert len(normalized) == 2
    assert normalized[0].startswith("CREATE TABLE USERS (")
    assert normalized[1].startswith("CREATE TABLE AUTH_SESSIONS (")
    assert (
        "ID BIGINT UNSIGNED NOT NULL AUTO_INCREMENT" in normalized[0]
        and "ID BIGINT UNSIGNED NOT NULL AUTO_INCREMENT" in normalized[1]
        and "USER_ID BIGINT UNSIGNED NOT NULL" in normalized[1]
    )
    assert (
        "EMAIL VARCHAR(254) CHARACTER SET UTF8MB4 "
        "COLLATE UTF8MB4_0900_BIN NOT NULL"
    ) in normalized[0]
    assert "CONSTRAINT UQ_USERS_EMAIL UNIQUE (EMAIL)" in normalized[0]
    assert "CONSTRAINT CK_USERS_EMAIL_NORMALIZED CHECK" in normalized[0]
    assert (
        "LENGTH(EMAIL) BETWEEN 3 AND 254 AND EMAIL = TRIM(EMAIL) "
        "AND EMAIL = LOWER(EMAIL)"
    ) in normalized[0]
    assert (
        "PASSWORD_HASH VARCHAR(255) CHARACTER SET ASCII "
        "COLLATE ASCII_BIN NOT NULL"
    ) in normalized[0]
    assert "CONSTRAINT CK_USERS_PASSWORD_HASH_NOT_EMPTY CHECK" in normalized[0]
    assert "LENGTH(PASSWORD_HASH) > 0" in normalized[0]
    assert "EMAIL_VERIFIED_AT DATETIME(6) NULL" in normalized[0]
    assert "TOKEN_HASH VARBINARY(32) NOT NULL" in normalized[1]
    assert (
        "CONSTRAINT UQ_AUTH_SESSIONS_TOKEN_HASH UNIQUE (TOKEN_HASH)"
        in normalized[1]
    )
    assert (
        "CONSTRAINT CK_AUTH_SESSIONS_TOKEN_HASH_LENGTH CHECK"
        in normalized[1]
    )
    assert "OCTET_LENGTH(TOKEN_HASH) = 32" in normalized[1]
    assert "REFERENCES USERS (ID)" in normalized[1]
    assert "ON DELETE RESTRICT" in normalized[1]
    assert "ON UPDATE RESTRICT" in normalized[1]
    assert (
        "CONSTRAINT CK_AUTH_SESSIONS_EXPIRES_AFTER_CREATED CHECK"
        in normalized[1]
    )
    assert "EXPIRES_AT > CREATED_AT" in normalized[1]
    assert "CONSTRAINT CK_AUTH_SESSIONS_LAST_SEEN_RANGE CHECK" in normalized[1]
    assert (
        "LAST_SEEN_AT >= CREATED_AT AND LAST_SEEN_AT <= EXPIRES_AT"
        in normalized[1]
    )
    assert "CONSTRAINT CK_AUTH_SESSIONS_REVOKED_AFTER_CREATED CHECK" in normalized[1]
    assert "REVOKED_AT IS NULL OR REVOKED_AT >= CREATED_AT" in normalized[1]
    assert (
        "INDEX IX_AUTH_SESSIONS_USER_STATE_EXPIRY "
        "( USER_ID, REVOKED_AT, EXPIRES_AT )"
    ) in normalized[1]
    assert "INDEX IX_AUTH_SESSIONS_EXPIRES_AT (EXPIRES_AT)" in normalized[1]
    assert normalized[0].count("DATETIME(6)") == 4
    assert normalized[1].count("DATETIME(6)") == 4
    assert all("ENGINE=INNODB" in statement for statement in normalized)
    assert all(
        "DEFAULT CHARACTER SET=UTF8MB4 COLLATE=UTF8MB4_0900_AI_CI" in statement
        for statement in normalized
    )
    assert "IF NOT EXISTS" not in executable_sql.upper()
    for forbidden in (
        "CREATE DATABASE",
        "DROP DATABASE",
        "DROP TABLE",
        "TRUNCATE",
        "GRANT ",
        "USE ",
    ):
        assert forbidden not in executable_sql.upper()


def test_auth_session_foreign_key_contract_is_restrictive() -> None:
    foreign_keys = list(AuthSession.__table__.foreign_keys)

    assert len(foreign_keys) == 1
    foreign_key = foreign_keys[0]
    assert foreign_key.target_fullname == "users.id"
    assert foreign_key.ondelete == "RESTRICT"
    assert foreign_key.onupdate == "RESTRICT"
