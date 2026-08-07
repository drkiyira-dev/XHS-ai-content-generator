"""Model-backed generation services."""

from backend.services.model.client import SiliconFlowClient
from backend.services.model.ocr import (
    OCRAugmentedGenerationService,
    PaddleOCRService,
)
from backend.services.model.qwen import QwenGenerationService, parse_generated_copy
from backend.services.model.types import GeneratedCopy, GenerationModelService


__all__ = [
    "GeneratedCopy",
    "GenerationModelService",
    "OCRAugmentedGenerationService",
    "PaddleOCRService",
    "QwenGenerationService",
    "SiliconFlowClient",
    "parse_generated_copy",
]
