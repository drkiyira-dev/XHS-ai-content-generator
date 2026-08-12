"""Persistence boundary for the generation lifecycle."""

from backend.services.persistence.noop import NoOpGenerationPersistence
from backend.services.persistence.sqlalchemy import SQLAlchemyGenerationPersistence
from backend.services.persistence.types import (
    DeletedGeneration,
    FailedGeneration,
    GenerationPersistence,
    GenerationPersistenceError,
    PendingGeneration,
    StoredGeneration,
    StoredImagePreview,
    SuccessfulGeneration,
)


__all__ = [
    "DeletedGeneration",
    "FailedGeneration",
    "GenerationPersistence",
    "GenerationPersistenceError",
    "NoOpGenerationPersistence",
    "PendingGeneration",
    "SQLAlchemyGenerationPersistence",
    "StoredGeneration",
    "StoredImagePreview",
    "SuccessfulGeneration",
]
