"""Async B-to-C adapter for the synchronous SQLAlchemy database core."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar

from anyio import to_thread
from sqlalchemy.orm import Session, sessionmaker

from backend.db import (
    create_pending as create_pending_record,
    mark_failed as mark_failed_record,
    mark_success as mark_success_record,
)
from backend.services.persistence.types import (
    FailedGeneration,
    GenerationPersistenceError,
    PendingGeneration,
    SuccessfulGeneration,
)


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
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock

    async def create_pending(self, record: PendingGeneration) -> None:
        """Persist B's identifier before the model call starts."""
        await self._execute(self._create_pending, record)

    async def mark_success(self, record: SuccessfulGeneration) -> None:
        """Run C validation and atomically persist B's generated copy."""
        await self._execute(self._mark_success, record)

    async def mark_failed(self, record: FailedGeneration) -> None:
        """Persist one stable failure code without private exception text."""
        await self._execute(self._mark_failed, record)

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
