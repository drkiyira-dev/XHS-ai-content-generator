"""Member-C database validation errors used below the HTTP boundary.

This module selectively integrates the reusable schema concepts from member C's
PR #1 at commit ``b4ca421``.  These exceptions are internal; the later B-owned
adapter must translate them before they can reach FastAPI.
"""

from typing import Any

from backend.schemas.risk_snapshot import (
    RiskAssessmentSnapshot,
    RiskFindingSnapshot,
)


class ErrorCode:
    """Stable codes raised by the database core."""

    DATABASE_ERROR = "DATABASE_ERROR"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"


class BusinessException(Exception):
    """Internal member-C business error with optional structured details."""

    def __init__(
        self,
        code: str,
        message: str,
        details: Any = None,
    ) -> None:
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


__all__ = [
    "BusinessException",
    "ErrorCode",
    "RiskAssessmentSnapshot",
    "RiskFindingSnapshot",
]
