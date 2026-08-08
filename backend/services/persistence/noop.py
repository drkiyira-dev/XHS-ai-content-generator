"""Temporary persistence implementation used before member-C integration."""

from backend.services.persistence.types import (
    FailedGeneration,
    PendingGeneration,
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
