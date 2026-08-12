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
    MAX_IMAGE_PREVIEW_BYTES,
    TASK_STATUS_SUCCESS,
    create_pending as create_pending_record,
    delete_successful as delete_successful_record,
    get_record as get_record_record,
    mark_failed as mark_failed_record,
    mark_success as mark_success_record,
)
from backend.services.persistence.types import (
    DeletedGeneration,
    FailedGeneration,
    GenerationPersistenceError,
    PendingGeneration,
    StoredGeneration,
    StoredImagePreview,
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

    async def list_successful(
        self,
        *,
        user_id: int | None,
        limit: int,
    ) -> tuple[StoredGeneration, ...]:
        """Read one user's successful generations without blocking the loop."""
        result: tuple[StoredGeneration, ...] | None = None
        failed = False
        try:
            result = await to_thread.run_sync(
                self._list_successful,
                user_id,
                limit,
                abandon_on_cancel=False,
                limiter=self._limiter,
            )
        except Exception:
            failed = True

        if failed or result is None:
            raise GenerationPersistenceError()
        return result

    async def get_successful(
        self,
        *,
        generation_id: str,
        user_id: int | None,
    ) -> StoredGeneration | None:
        """Read one visible success without exposing another owner partition."""
        result: StoredGeneration | None = None
        failed = False
        try:
            result = await to_thread.run_sync(
                self._get_successful,
                generation_id,
                user_id,
                abandon_on_cancel=False,
                limiter=self._limiter,
            )
        except Exception:
            failed = True
        if failed:
            raise GenerationPersistenceError()
        return result

    async def get_image_preview(
        self,
        *,
        generation_id: str,
        user_id: int | None,
    ) -> StoredImagePreview | None:
        """Read bounded preview bytes only for a visible owned success."""
        result: StoredImagePreview | None = None
        failed = False
        try:
            result = await to_thread.run_sync(
                self._get_image_preview,
                generation_id,
                user_id,
                abandon_on_cancel=False,
                limiter=self._limiter,
            )
        except Exception:
            failed = True
        if failed:
            raise GenerationPersistenceError()
        return result

    async def delete_successful(
        self,
        *,
        generation_id: str,
        user_id: int | None,
        deleted_at: datetime,
    ) -> DeletedGeneration | None:
        """Run one owner-scoped idempotent soft deletion off the event loop."""
        result: DeletedGeneration | None = None
        failed = False
        try:
            result = await to_thread.run_sync(
                self._delete_successful,
                generation_id,
                user_id,
                deleted_at,
                abandon_on_cancel=False,
                limiter=self._limiter,
            )
        except Exception:
            failed = True
        if failed:
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
                user_id=record.user_id,
                created_at=record.created_at,
            )

    def _mark_success(self, record: SuccessfulGeneration) -> None:
        with self._session_factory.begin() as session:
            mark_success_record(
                session,
                generation_id=record.generation_id,
                user_id=record.user_id,
                image_summary=record.image_summary,
                title=record.title,
                body=record.body,
                tags=record.tags,
                completed_at=self._clock(),
                image_preview=record.image_preview,
                image_preview_media_type=record.image_preview_media_type,
            )

    def _mark_failed(self, record: FailedGeneration) -> None:
        with self._session_factory.begin() as session:
            mark_failed_record(
                session,
                generation_id=record.generation_id,
                user_id=record.user_id,
                error_code=record.error_code,
                failed_at=record.failed_at,
            )

    def _list_successful(
        self,
        user_id: int | None,
        limit: int,
    ) -> tuple[StoredGeneration, ...]:
        normalized_user_id = _validate_user_id(user_id)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 50
        ):
            raise ValueError("history limit is outside the supported range")

        with self._session_factory() as session:
            records = session.execute(
                select(GenerationRecord)
                .where(
                    _owner_predicate(normalized_user_id),
                    GenerationRecord.status == TASK_STATUS_SUCCESS,
                    GenerationRecord.deleted_at.is_(None),
                )
                .order_by(
                    GenerationRecord.created_at.desc(),
                    GenerationRecord.id.desc(),
                )
                .limit(limit)
            ).scalars()
            return tuple(
                self._to_stored_generation(record, normalized_user_id)
                for record in records
            )

    @staticmethod
    def _to_stored_generation(
        record: GenerationRecord,
        expected_user_id: int | None,
    ) -> StoredGeneration:
        record_user_id = _validate_user_id(record.user_id)
        if record_user_id != expected_user_id:
            raise ValueError("successful database record has the wrong owner")
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
            user_id=record_user_id,
            image_summary=summary,
            title=title,
            body=body,
            tags=tuple(tags),
            created_at=created_at,
            has_image_preview=(
                record.image_preview_media_type in {"image/jpeg", "image/webp"}
            ),
        )

    def _get_successful(
        self,
        generation_id: str,
        user_id: int | None,
    ) -> StoredGeneration | None:
        normalized_user_id = _validate_user_id(user_id)
        with self._session_factory() as session:
            record = get_record_record(
                session,
                generation_id=generation_id,
                user_id=normalized_user_id,
            )
            if record is None or record.status != TASK_STATUS_SUCCESS:
                return None
            return self._to_stored_generation(record, normalized_user_id)

    def _get_image_preview(
        self,
        generation_id: str,
        user_id: int | None,
    ) -> StoredImagePreview | None:
        normalized_user_id = _validate_user_id(user_id)
        with self._session_factory() as session:
            row = session.execute(
                select(
                    GenerationRecord.task_id,
                    GenerationRecord.user_id,
                    GenerationRecord.image_preview,
                    GenerationRecord.image_preview_media_type,
                ).where(
                    GenerationRecord.task_id == generation_id,
                    _owner_predicate(normalized_user_id),
                    GenerationRecord.status == TASK_STATUS_SUCCESS,
                    GenerationRecord.deleted_at.is_(None),
                )
            ).one_or_none()
            if row is None:
                return None
            if str(UUID(row.task_id)) != row.task_id:
                raise ValueError("preview database record has an invalid identifier")
            if _validate_user_id(row.user_id) != normalized_user_id:
                raise ValueError("preview database record has the wrong owner")
            preview = _extract_image_preview(
                row.image_preview,
                row.image_preview_media_type,
            )
            if preview is None:
                return None
            content, media_type = preview
            return StoredImagePreview(
                generation_id=row.task_id,
                user_id=normalized_user_id,
                content=content,
                media_type=media_type,
            )

    def _delete_successful(
        self,
        generation_id: str,
        user_id: int | None,
        deleted_at: datetime,
    ) -> DeletedGeneration | None:
        normalized_user_id = _validate_user_id(user_id)
        with self._session_factory.begin() as session:
            deleted_now = delete_successful_record(
                session,
                generation_id=generation_id,
                user_id=normalized_user_id,
                deleted_at=deleted_at,
            )
            if deleted_now is None:
                return None
            return DeletedGeneration(
                generation_id=generation_id,
                user_id=normalized_user_id,
                deleted_now=deleted_now,
            )


def _validate_user_id(user_id: object) -> int | None:
    if user_id is None:
        return None
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("user_id is invalid")
    return user_id


def _owner_predicate(user_id: int | None):
    """Keep the legacy NULL partition explicit instead of dropping ownership."""
    if user_id is None:
        return GenerationRecord.user_id.is_(None)
    return GenerationRecord.user_id == user_id


def _extract_image_preview(
    content: object,
    media_type: object,
) -> tuple[bytes, str] | None:
    """Treat malformed legacy/admin-written payloads as unavailable previews."""
    if (
        not isinstance(content, bytes)
        or not 1 <= len(content) <= MAX_IMAGE_PREVIEW_BYTES
        or not isinstance(media_type, str)
        or media_type not in {"image/jpeg", "image/webp"}
    ):
        return None
    if media_type == "image/jpeg":
        valid = (
            len(content) >= 4
            and content.startswith(b"\xff\xd8\xff")
            and content.endswith(b"\xff\xd9")
        )
    else:
        valid = (
            len(content) >= 12
            and content.startswith(b"RIFF")
            and content[8:12] == b"WEBP"
        )
    return (content, media_type) if valid else None
