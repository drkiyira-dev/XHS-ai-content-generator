"""FastAPI application factory."""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Response
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
from backend.services.persistence.runtime import (
    SQLAlchemyPersistenceRuntime,
    create_sqlalchemy_persistence_runtime,
)


logger = logging.getLogger(__name__)


async def _close_database_runtime_best_effort(
    runtime: SQLAlchemyPersistenceRuntime,
) -> None:
    """Release the Engine without replacing an earlier startup/application error."""
    try:
        await runtime.aclose()
    except Exception as error:
        logger.error(
            "Database runtime shutdown failed type=%s",
            type(error).__name__,
        )


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

    fallback_generation_persistence = (
        generation_persistence
        if generation_persistence is not None
        else NoOpGenerationPersistence()
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        owned_database_runtime: SQLAlchemyPersistenceRuntime | None = None
        try:
            if generation_persistence is None and settings.database_enabled:
                owned_database_runtime = create_sqlalchemy_persistence_runtime(
                    settings
                )
                await owned_database_runtime.startup()
                application.state.generation_persistence = (
                    owned_database_runtime.persistence
                )
            yield
        finally:
            application.state.generation_persistence = (
                fallback_generation_persistence
            )
            try:
                if owned_database_runtime is not None:
                    await _close_database_runtime_best_effort(
                        owned_database_runtime
                    )
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
    application.state.generation_persistence = fallback_generation_persistence
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
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @application.get(
        "/api/health",
        tags=["health"],
        summary="Check API liveness",
    )
    async def health(response: Response) -> dict[str, str]:
        """Report process liveness without contacting models or persistence."""
        response.headers["Cache-Control"] = "no-store"
        return {"status": "ok"}

    application.include_router(api_v1_router, prefix="/api/v1")
    return application
