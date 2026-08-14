"""B-owned contracts for persisting one generation lifecycle."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from backend.schemas import RiskAssessmentSnapshot


@dataclass(frozen=True, slots=True)
class PendingGeneration:
    """Minimal record created before the model request starts."""

    generation_id: str
    user_id: int | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SuccessfulGeneration:
    """Validated model output ready to be stored as a success."""

    generation_id: str
    user_id: int | None
    image_summary: str
    title: str
    body: str
    tags: tuple[str, ...]
    risk_assessment: RiskAssessmentSnapshot
    image_preview: bytes | None = field(default=None, repr=False)
    image_preview_media_type: str | None = None


@dataclass(frozen=True, slots=True)
class FailedGeneration:
    """Safe failure metadata that never contains private exception text."""

    generation_id: str
    user_id: int | None
    error_code: str
    failed_at: datetime


@dataclass(frozen=True, slots=True)
class StoredGeneration:
    """One successful generation safe to expose in local history."""

    generation_id: str
    user_id: int | None
    image_summary: str
    title: str
    body: str
    tags: tuple[str, ...]
    created_at: datetime
    risk_assessment: RiskAssessmentSnapshot | None = None
    has_image_preview: bool = False


@dataclass(frozen=True, slots=True)
class StoredImagePreview:
    """Owner-scoped preview bytes; never serialize this object as history JSON."""

    generation_id: str
    user_id: int | None
    content: bytes = field(repr=False)
    media_type: str


@dataclass(frozen=True, slots=True)
class DeletedGeneration:
    """Internal result of an owner-scoped, idempotent soft deletion."""

    generation_id: str
    user_id: int | None
    deleted_now: bool


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

    async def list_successful(
        self,
        *,
        user_id: int | None,
        limit: int,
    ) -> tuple[StoredGeneration, ...]:
        """Return one user's recent successful generations, newest first."""
        ...

    async def get_successful(
        self,
        *,
        generation_id: str,
        user_id: int | None,
    ) -> StoredGeneration | None:
        """Return one visible successful generation inside its owner partition."""
        ...

    async def get_image_preview(
        self,
        *,
        generation_id: str,
        user_id: int | None,
    ) -> StoredImagePreview | None:
        """Return private preview bytes only for a visible owned success."""
        ...

    async def delete_successful(
        self,
        *,
        generation_id: str,
        user_id: int | None,
        deleted_at: datetime,
    ) -> DeletedGeneration | None:
        """Soft-delete one owned success, succeeding again after prior deletion."""
        ...
