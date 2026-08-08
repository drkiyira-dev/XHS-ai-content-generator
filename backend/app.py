"""FastAPI application factory."""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.errors import APIError, register_exception_handlers
from backend.api.v1 import router as api_v1_router
from backend.core.config import Settings
from backend.services.model import (
    GenerationModelService,
    OCRAugmentedGenerationService,
    PaddleOCRService,
    QwenGenerationService,
    SiliconFlowClient,
)
from backend.services.persistence import (
    GenerationPersistence,
    NoOpGenerationPersistence,
)


logger = logging.getLogger(__name__)


def create_app(
    settings: Settings,
    *,
    model_service: GenerationModelService | None = None,
    generation_persistence: GenerationPersistence | None = None,
) -> FastAPI:
    """Build an application with explicit, testable runtime settings."""
    owned_model_service: OCRAugmentedGenerationService | None = None
    runtime_model_service = model_service
    if runtime_model_service is None:
        provider_client = SiliconFlowClient(settings)
        owned_model_service = OCRAugmentedGenerationService(
            settings,
            PaddleOCRService(settings, provider_client),
            QwenGenerationService(settings, provider_client),
        )
        runtime_model_service = owned_model_service

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        try:
            yield
        finally:
            if owned_model_service is not None:
                await owned_model_service.aclose()

    application = FastAPI(
        title="XHS AI Content Generator API",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.state.model_service = runtime_model_service
    application.state.generation_persistence = (
        generation_persistence
        if generation_persistence is not None
        else NoOpGenerationPersistence()
    )
    register_exception_handlers(application)

    @application.middleware("http")
    async def convert_unexpected_errors(request, call_next):
        """Create the safe 500 response inside CORS middleware."""
        try:
            return await call_next(request)
        except Exception as error:
            logger.error(
                "Unhandled API error type=%s method=%s path=%s",
                type(error).__name__,
                request.method,
                request.url.path,
            )
            api_error = APIError(
                code="INTERNAL_ERROR",
                message="服务器内部错误，请稍后重试。",
                status_code=500,
            )
            return JSONResponse(
                status_code=api_error.status_code,
                content=api_error.payload(),
            )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["POST", "OPTIONS"],
        allow_headers=["*"],
    )
    application.include_router(api_v1_router, prefix="/api/v1")
    return application
