"""FastAPI application factory."""

from contextlib import asynccontextmanager
import logging
from urllib.parse import urlsplit

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.api.errors import APIError, register_exception_handlers
from backend.api.v1 import router as api_v1_router
from backend.core.config import Settings
from backend.services.auth import (
    AuthenticationService,
    AuthService,
)
from backend.services.auth.rate_limit import AuthRateLimiter
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
    DatabaseStartupError,
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
    auth_service: AuthenticationService | None = None,
    auth_rate_limiter: AuthRateLimiter | None = None,
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
    fallback_auth_service = auth_service if settings.auth_enabled else None
    runtime_auth_rate_limiter = (
        auth_rate_limiter or AuthRateLimiter()
        if settings.auth_enabled
        else None
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        owned_database_runtime: SQLAlchemyPersistenceRuntime | None = None
        try:
            require_generation = bool(
                generation_persistence is None and settings.database_enabled
            )
            require_auth = bool(
                auth_service is None and settings.auth_enabled
            )
            if require_generation or require_auth:
                if settings.auth_enabled:
                    owned_database_runtime = create_sqlalchemy_persistence_runtime(
                        settings,
                        require_generation=require_generation,
                        require_auth=require_auth,
                    )
                else:
                    # Preserve the original generation-only factory contract.
                    owned_database_runtime = create_sqlalchemy_persistence_runtime(
                        settings
                    )
                await owned_database_runtime.startup()
                if require_generation:
                    application.state.generation_persistence = (
                        owned_database_runtime.persistence
                    )
                if require_auth:
                    auth_store = owned_database_runtime.auth_store
                    if auth_store is None:
                        raise DatabaseStartupError() from None
                    application.state.auth_service = AuthService(auth_store)
            yield
        finally:
            application.state.generation_persistence = (
                fallback_generation_persistence
            )
            application.state.auth_service = fallback_auth_service
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
    application.state.auth_service = fallback_auth_service
    application.state.auth_rate_limiter = runtime_auth_rate_limiter
    register_exception_handlers(application)

    @application.middleware("http")
    async def convert_unexpected_errors(request, call_next):
        """Create the safe 500 response inside CORS middleware."""
        try:
            response = await call_next(request)
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
            response = JSONResponse(
                status_code=api_error.status_code,
                content=api_error.payload(),
            )
        return response

    cors_options: dict[str, object] = {
        "allow_origins": list(settings.cors_origins),
        "allow_credentials": settings.auth_enabled,
        "allow_methods": ["DELETE", "GET", "POST", "OPTIONS"],
        "allow_headers": (
            ["Accept", "Content-Type", "X-XHS-CSRF"]
            if settings.auth_enabled
            else ["*"]
        ),
    }
    application.add_middleware(CORSMiddleware, **cors_options)
    if settings.auth_enabled:
        application.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=list(_trusted_hosts(settings)),
            www_redirect=False,
        )
        application.add_middleware(_AuthNoStoreMiddleware)

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
    if settings.auth_enabled:
        from backend.api.v1.auth import router as auth_router

        application.include_router(auth_router, prefix="/api/v1")
        _remove_unreachable_auth_validation_responses(application)
    return application


def _is_auth_path(path: str) -> bool:
    """Match only the versioned authentication route namespace."""
    return path == "/api/v1/auth" or path.startswith("/api/v1/auth/")


class _AuthNoStoreMiddleware:
    """Apply no-store even when outer CORS or host checks create the response."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http" or not _is_auth_path(scope.get("path", "")):
            await self._app(scope, receive, send)
            return

        async def send_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["Pragma"] = "no-cache"
            await send(message)

        await self._app(scope, receive, send_no_store)


def _trusted_hosts(settings: Settings) -> tuple[str, ...]:
    """Allow only configured browser hosts plus local API test hosts."""
    hosts = {"localhost", "127.0.0.1", "testserver"}
    for origin in settings.cors_origins:
        host = urlsplit(origin).hostname
        if host and ":" not in host:
            hosts.add(host)
    return tuple(sorted(hosts))


def _remove_unreachable_auth_validation_responses(
    application: FastAPI,
) -> None:
    """Keep OpenAPI aligned with fixed 400/401 auth validation responses."""
    schema = application.openapi()
    paths = schema.get("paths", {})
    for path, method in (
        ("/api/v1/auth/register", "post"),
        ("/api/v1/auth/login", "post"),
    ):
        operation = paths.get(path, {}).get(method, {})
        responses = operation.get("responses", {})
        responses.pop("422", None)
