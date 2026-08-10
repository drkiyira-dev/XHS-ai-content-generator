"""B-owned contracts for persisting one generation lifecycle."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PendingGeneration:
    """Minimal record created before the model request starts."""

    generation_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SuccessfulGeneration:
    """Validated model output ready to be stored as a success."""

    generation_id: str
    image_summary: str
    title: str
    body: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FailedGeneration:
    """Safe failure metadata that never contains private exception text."""

    generation_id: str
    error_code: str
    failed_at: datetime


@dataclass(frozen=True, slots=True)
class StoredGeneration:
    """One successful generation safe to expose in local history."""

    generation_id: str
    image_summary: str
    title: str
    body: str
    tags: tuple[str, ...]
    created_at: datetime


class GenerationPersistenceError(Exception):
    """Expected storage failure without provider or database details."""

    def __init__(self) -> None:
        super().__init__("generation persistence failed")


class GenerationPersistence(Protocol):
    """Async boundary that a later member-C database adapter must implement."""

    async def create_pending(self, record: PendingGeneration) -> None:
        """Create the lifecycle record before model generation."""
        ...

    async def mark_success(self, record: SuccessfulGeneration) -> None:
        """Store validated copy and mark the lifecycle successful."""
        ...

    async def mark_failed(self, record: FailedGeneration) -> None:
        """Mark the lifecycle failed using only a stable error code."""
        ...

    async def list_successful(self, *, limit: int) -> tuple[StoredGeneration, ...]:
        """Return the most recent successful generations, newest first."""
        ...
