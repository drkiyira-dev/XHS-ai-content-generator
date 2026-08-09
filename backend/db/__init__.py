"""Safe subset of member C's database core for B+C integration."""

from backend.db.models import (
    Base,
    GenerationRecord,
    TASK_STATUS_FAILED,
    TASK_STATUS_PENDING,
    TASK_STATUS_SUCCESS,
)
from backend.db.repository import (
    create_pending,
    get_record,
    mark_failed,
    mark_success,
)


__all__ = [
    "Base",
    "GenerationRecord",
    "TASK_STATUS_FAILED",
    "TASK_STATUS_PENDING",
    "TASK_STATUS_SUCCESS",
    "create_pending",
    "get_record",
    "mark_failed",
    "mark_success",
]
