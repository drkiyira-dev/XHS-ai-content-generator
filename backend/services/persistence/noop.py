"""Temporary persistence implementation used before member-C integration."""

from datetime import datetime

from backend.services.persistence.types import (
    DeletedGeneration,
    FailedGeneration,
    GenerationPersistenceError,
    PendingGeneration,
    StoredGeneration,
    StoredImagePreview,
    SuccessfulGeneration,
)


class NoOpGenerationPersistence:
    """Preserve current API behavior without pretending data was stored."""

    async def create_pending(self, record: PendingGeneration) -> None:
        _validate_user_id(record.user_id)

    async def mark_success(self, record: SuccessfulGeneration) -> None:
        _validate_user_id(record.user_id)

    async def mark_failed(self, record: FailedGeneration) -> None:
        _validate_user_id(record.user_id)

    async def list_successful(
        self,
        *,
        user_id: int | None,
        limit: int,
    ) -> tuple[StoredGeneration, ...]:
        _validate_user_id(user_id)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 50
        ):
            raise GenerationPersistenceError()
        return ()

    async def get_successful(
        self,
        *,
        generation_id: str,
        user_id: int | None,
    ) -> StoredGeneration | None:
        _validate_generation_id(generation_id)
        _validate_user_id(user_id)
        return None

    async def get_image_preview(
        self,
        *,
        generation_id: str,
        user_id: int | None,
    ) -> StoredImagePreview | None:
        _validate_generation_id(generation_id)
        _validate_user_id(user_id)
        return None

    async def delete_successful(
        self,
        *,
        generation_id: str,
        user_id: int | None,
        deleted_at: datetime,
    ) -> DeletedGeneration | None:
        _validate_generation_id(generation_id)
        _validate_user_id(user_id)
        if not isinstance(deleted_at, datetime) or deleted_at.tzinfo is None:
            raise GenerationPersistenceError()
        return None


def _validate_user_id(user_id: object) -> None:
    """Require an explicit legacy NULL owner or a positive account identifier."""
    if user_id is None:
        return
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise GenerationPersistenceError()


def _validate_generation_id(generation_id: object) -> None:
    if (
        not isinstance(generation_id, str)
        or not generation_id
        or len(generation_id) > 64
        or generation_id != generation_id.strip()
    ):
        raise GenerationPersistenceError()
