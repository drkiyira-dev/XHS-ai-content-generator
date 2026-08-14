"""SQLAlchemy models for generation persistence and local accounts."""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT as MYSQL_BIGINT
from sqlalchemy.dialects.mysql import DATETIME as MYSQL_DATETIME
from sqlalchemy.dialects.mysql import MEDIUMBLOB as MYSQL_MEDIUMBLOB
from sqlalchemy.dialects.mysql import VARBINARY as MYSQL_VARBINARY
from sqlalchemy.dialects.mysql import VARCHAR as MYSQL_VARCHAR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


TASK_STATUS_PENDING = "pending"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_FAILED = "failed"
MAX_IMAGE_PREVIEW_BYTES = 256 * 1024


def _utc_now() -> datetime:
    """Return naive UTC for MySQL DATETIME compatibility."""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Declarative base shared by all application-owned database tables."""


def _account_id_type():
    """Use unsigned BIGINT in MySQL and an auto-incrementing INTEGER in SQLite."""
    return (
        BigInteger()
        .with_variant(Integer(), "sqlite")
        .with_variant(MYSQL_BIGINT(unsigned=True), "mysql")
    )


def _email_type():
    """Keep normalized email comparisons byte-exact in MySQL."""
    return String(254).with_variant(
        MYSQL_VARCHAR(254, charset="utf8mb4", collation="utf8mb4_0900_bin"),
        "mysql",
    )


def _password_hash_type():
    """Store only the ASCII encoded password-hash representation."""
    return String(255).with_variant(
        MYSQL_VARCHAR(255, charset="ascii", collation="ascii_bin"),
        "mysql",
    )


def _session_token_hash_type():
    """Store one exact 32-byte SHA-256 session-token digest."""
    return LargeBinary(32).with_variant(MYSQL_VARBINARY(32), "mysql")


def _image_preview_type():
    """Persist one bounded preview in MySQL, not in the ephemeral upload tmpfs."""
    return LargeBinary().with_variant(MYSQL_MEDIUMBLOB(), "mysql")


def _image_preview_media_type():
    """Store only an exact, allow-listed ASCII media type."""
    return String(32).with_variant(
        MYSQL_VARCHAR(32, charset="ascii", collation="ascii_bin"),
        "mysql",
    )


def _utc_datetime_type():
    """Preserve UTC microseconds in MySQL while remaining portable to SQLite."""
    return DateTime(timezone=False).with_variant(MYSQL_DATETIME(fsp=6), "mysql")


class User(Base):
    """Local demonstration account identified by a normalized email address."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint(
            "length(email) BETWEEN 3 AND 254 "
            "AND email = trim(email) AND email = lower(email)",
            name="ck_users_email_normalized",
        ),
        CheckConstraint(
            "is_active IN (0, 1)",
            name="ck_users_is_active",
        ),
        CheckConstraint(
            "length(password_hash) > 0",
            name="ck_users_password_hash_not_empty",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="ck_users_updated_after_created",
        ),
        CheckConstraint(
            "email_verified_at IS NULL OR email_verified_at >= created_at",
            name="ck_users_verified_after_created",
        ),
        CheckConstraint(
            "last_login_at IS NULL OR last_login_at >= created_at",
            name="ck_users_login_after_created",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(
        _account_id_type(),
        primary_key=True,
        autoincrement=True,
    )
    email: Mapped[str] = mapped_column(_email_type(), nullable=False)
    password_hash: Mapped[str] = mapped_column(
        _password_hash_type(),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("1"),
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        _utc_datetime_type(),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        _utc_datetime_type(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        _utc_datetime_type(),
        nullable=False,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        _utc_datetime_type(),
        nullable=True,
    )


class AuthSession(Base):
    """Revocable login session containing only a hash of the cookie token."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
        CheckConstraint(
            "length(token_hash) = 32",
            name="ck_auth_sessions_token_hash_length",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_auth_sessions_expires_after_created",
        ),
        CheckConstraint(
            "last_seen_at >= created_at AND last_seen_at <= expires_at",
            name="ck_auth_sessions_last_seen_range",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_auth_sessions_revoked_after_created",
        ),
        Index(
            "ix_auth_sessions_user_state_expiry",
            "user_id",
            "revoked_at",
            "expires_at",
        ),
        Index("ix_auth_sessions_expires_at", "expires_at"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(
        _account_id_type(),
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[int] = mapped_column(
        _account_id_type(),
        ForeignKey(
            "users.id",
            name="fk_auth_sessions_user_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    token_hash: Mapped[bytes] = mapped_column(
        _session_token_hash_type(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        _utc_datetime_type(),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        _utc_datetime_type(),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        _utc_datetime_type(),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        _utc_datetime_type(),
        nullable=True,
    )


class GenerationRecord(Base):
    """One generation lifecycle stored under B's generation identifier."""

    __tablename__ = "generation_records"
    __table_args__ = (
        CheckConstraint(
            "(image_preview IS NULL AND image_preview_media_type IS NULL) OR "
            "(image_preview IS NOT NULL AND "
            "image_preview_media_type IS NOT NULL AND "
            "image_preview_media_type IN ('image/webp', 'image/jpeg') AND "
            f"length(image_preview) BETWEEN 1 AND {MAX_IMAGE_PREVIEW_BYTES})",
            name="ck_generation_records_image_preview",
        ),
        CheckConstraint(
            "deleted_at IS NULL OR deleted_at >= created_at",
            name="ck_generation_records_deleted_after_created",
        ),
        Index(
            "ix_generation_records_user_status_created",
            "user_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        _account_id_type(),
        ForeignKey(
            "users.id",
            name="fk_generation_records_user_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    task_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=TASK_STATUS_PENDING,
        index=True,
    )
    image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    image_preview: Mapped[bytes | None] = mapped_column(
        _image_preview_type(),
        nullable=True,
        deferred=True,
    )
    image_preview_media_type: Mapped[str | None] = mapped_column(
        _image_preview_media_type(),
        nullable=True,
    )
    image_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    risk_assessment: Mapped[dict[str, object] | None] = mapped_column(
        JSON(none_as_null=True),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        default=_utc_now,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        _utc_datetime_type(),
        nullable=True,
    )

    @property
    def generation_id(self) -> str:
        """Expose C's task_id using the name frozen by B's API contract."""
        return self.task_id

    def to_dict(self) -> dict[str, object]:
        """Return both B and C field aliases without changing stored data."""
        return {
            "id": self.id,
            "generation_id": self.task_id,
            "task_id": self.task_id,
            "status": self.status,
            "image_path": self.image_path,
            "has_image_preview": (
                self.image_preview_media_type in {"image/jpeg", "image/webp"}
            ),
            "image_description": self.image_description,
            "image_summary": self.image_description,
            "user_input": self.user_input,
            "title": self.title,
            "content": self.content,
            "body": self.content,
            "tags": list(self.tags) if self.tags is not None else None,
            "risk_assessment": self.risk_assessment,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deleted_at": self.deleted_at,
        }
