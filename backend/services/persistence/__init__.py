"""Persistence boundary for the generation lifecycle."""

from backend.services.persistence.noop import NoOpGenerationPersistence
from backend.services.persistence.sqlalchemy import SQLAlchemyGenerationPersistence
from backend.services.persistence.types import (
    FailedGeneration,
    GenerationPersistence,
    GenerationPersistenceError,
    PendingGeneration,
    StoredGeneration,
    SuccessfulGeneration,
)


__all__ = [
    "FailedGeneration",
    "GenerationPersistence",
    "GenerationPersistenceError",
    "NoOpGenerationPersistence",
    "PendingGeneration",
    "SQLAlchemyGenerationPersistence",
    "StoredGeneration",
    "SuccessfulGeneration",
]
