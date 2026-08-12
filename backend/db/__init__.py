"""Application database models and generation repository operations."""

from backend.db.models import (
    AuthSession,
    Base,
    GenerationRecord,
    MAX_IMAGE_PREVIEW_BYTES,
    TASK_STATUS_FAILED,
    TASK_STATUS_PENDING,
    TASK_STATUS_SUCCESS,
    User,
)
from backend.db.repository import (
    create_pending,
    delete_successful,
    get_record,
    mark_failed,
    mark_success,
)


__all__ = [
    "AuthSession",
    "Base",
    "GenerationRecord",
    "MAX_IMAGE_PREVIEW_BYTES",
    "TASK_STATUS_FAILED",
    "TASK_STATUS_PENDING",
    "TASK_STATUS_SUCCESS",
    "User",
    "create_pending",
    "delete_successful",
    "get_record",
    "mark_failed",
    "mark_success",
]
