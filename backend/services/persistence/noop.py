"""Temporary persistence implementation used before member-C integration."""

from backend.services.persistence.types import (
    FailedGeneration,
    GenerationPersistenceError,
    PendingGeneration,
    StoredGeneration,
    SuccessfulGeneration,
)


class NoOpGenerationPersistence:
    """Preserve current API behavior without pretending data was stored."""

    async def create_pending(self, record: PendingGeneration) -> None:
        _ = record

    async def mark_success(self, record: SuccessfulGeneration) -> None:
        _ = record

    async def mark_failed(self, record: FailedGeneration) -> None:
        _ = record

    async def list_successful(self, *, limit: int) -> tuple[StoredGeneration, ...]:
        if not 1 <= limit <= 50:
            raise GenerationPersistenceError()
        return ()
