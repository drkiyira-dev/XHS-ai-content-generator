"""Persistence boundary for the generation lifecycle."""

from backend.services.persistence.noop import NoOpGenerationPersistence
from backend.services.persistence.types import (
    FailedGeneration,
    GenerationPersistence,
    GenerationPersistenceError,
    PendingGeneration,
    SuccessfulGeneration,
)


__all__ = [
    "FailedGeneration",
    "GenerationPersistence",
    "GenerationPersistenceError",
    "NoOpGenerationPersistence",
    "PendingGeneration",
    "SuccessfulGeneration",
]
