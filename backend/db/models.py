"""SQLAlchemy model selectively integrated from member C's PR #1."""

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


TASK_STATUS_PENDING = "pending"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_FAILED = "failed"


def _utc_now() -> datetime:
    """Return naive UTC for MySQL DATETIME compatibility."""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Declarative base for the member-C database tables."""


class GenerationRecord(Base):
    """One generation lifecycle stored under B's generation identifier."""

    __tablename__ = "generation_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
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
    image_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
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
            "image_description": self.image_description,
            "image_summary": self.image_description,
            "user_input": self.user_input,
            "title": self.title,
            "content": self.content,
            "body": self.content,
            "tags": list(self.tags) if self.tags is not None else None,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
