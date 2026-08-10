"""Async B-to-C adapter for the synchronous SQLAlchemy database core."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from anyio import CapacityLimiter, to_thread
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.db import (
    GenerationRecord,
    TASK_STATUS_SUCCESS,
    create_pending as create_pending_record,
    mark_failed as mark_failed_record,
    mark_success as mark_success_record,
)
from backend.services.persistence.types import (
    FailedGeneration,
    GenerationPersistenceError,
    PendingGeneration,
    StoredGeneration,
    SuccessfulGeneration,
)
from backend.validation import validate_copy


RecordT = TypeVar(
    "RecordT",
    PendingGeneration,
    SuccessfulGeneration,
    FailedGeneration,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SQLAlchemyGenerationPersistence:
    """Implement B's async protocol over member C's synchronous repository."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] = _utc_now,
        limiter: CapacityLimiter | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._limiter = limiter

    async def create_pending(self, record: PendingGeneration) -> None:
        """Persist B's identifier before the model call starts."""
        await self._execute(self._create_pending, record)

    async def mark_success(self, record: SuccessfulGeneration) -> None:
        """Run C validation and atomically persist B's generated copy."""
        await self._execute(self._mark_success, record)

    async def mark_failed(self, record: FailedGeneration) -> None:
        """Persist one stable failure code without private exception text."""
        await self._execute(self._mark_failed, record)

    async def list_successful(self, *, limit: int) -> tuple[StoredGeneration, ...]:
        """Read recent successful generations without blocking the event loop."""
        result: tuple[StoredGeneration, ...] | None = None
        failed = False
        try:
            result = await to_thread.run_sync(
                self._list_successful,
                limit,
                abandon_on_cancel=False,
                limiter=self._limiter,
            )
        except Exception:
            failed = True

        if failed or result is None:
            raise GenerationPersistenceError()
        return result

    async def _execute(
        self,
        operation: Callable[[RecordT], None],
        record: RecordT,
    ) -> None:
        """Run blocking work off-loop and erase all provider error context."""
        failed = False
        try:
            await to_thread.run_sync(
                operation,
                record,
                abandon_on_cancel=False,
                limiter=self._limiter,
            )
        except Exception:
            failed = True

        if failed:
            raise GenerationPersistenceError()

    def _create_pending(self, record: PendingGeneration) -> None:
        with self._session_factory.begin() as session:
            create_pending_record(
                session,
                generation_id=record.generation_id,
                created_at=record.created_at,
            )

    def _mark_success(self, record: SuccessfulGeneration) -> None:
        with self._session_factory.begin() as session:
            mark_success_record(
                session,
                generation_id=record.generation_id,
                image_summary=record.image_summary,
                title=record.title,
                body=record.body,
                tags=record.tags,
                completed_at=self._clock(),
            )

    def _mark_failed(self, record: FailedGeneration) -> None:
        with self._session_factory.begin() as session:
            mark_failed_record(
                session,
                generation_id=record.generation_id,
                error_code=record.error_code,
                failed_at=record.failed_at,
            )

    def _list_successful(self, limit: int) -> tuple[StoredGeneration, ...]:
        if not 1 <= limit <= 50:
            raise ValueError("history limit is outside the supported range")

        with self._session_factory() as session:
            records = session.execute(
                select(GenerationRecord)
                .where(GenerationRecord.status == TASK_STATUS_SUCCESS)
                .order_by(
                    GenerationRecord.created_at.desc(),
                    GenerationRecord.id.desc(),
                )
                .limit(limit)
            ).scalars()
            return tuple(self._to_stored_generation(record) for record in records)

    @staticmethod
    def _to_stored_generation(record: GenerationRecord) -> StoredGeneration:
        if str(UUID(record.task_id)) != record.task_id:
            raise ValueError("successful database record has an invalid identifier")

        summary, title, body, tags = validate_copy(
            image_summary=record.image_description,
            title=record.title,
            body=record.content,
            tags=record.tags,
        )
        if (
            summary != record.image_description
            or title != record.title
            or body != record.content
            or tags != record.tags
        ):
            raise ValueError("successful database record is not normalized")

        created_at = record.created_at
        if not isinstance(created_at, datetime):
            raise ValueError("successful database record has an invalid timestamp")
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        else:
            created_at = created_at.astimezone(UTC)

        return StoredGeneration(
            generation_id=record.task_id,
            image_summary=summary,
            title=title,
            body=body,
            tags=tuple(tags),
            created_at=created_at,
        )
