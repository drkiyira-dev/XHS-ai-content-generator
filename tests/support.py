"""Reusable app and image factories for backend tests."""

from io import BytesIO
from typing import Any

from fastapi import FastAPI
import httpx
from PIL import Image

from backend.app import create_app
from backend.core.config import Settings
from backend.services.auth import (
    AuthenticatedUser,
    AuthenticationService,
    IssuedSession,
)
from backend.services.image import ProcessedImage
from backend.services.model import GeneratedCopy, GenerationModelService
from backend.services.auth.rate_limit import AuthRateLimiter
from backend.services.persistence import (
    GenerationPersistence,
    NoOpGenerationPersistence,
)
from backend.schemas import RiskAssessmentSnapshot


DEFAULT_TEST_USER_ID = 101
DEFAULT_TEST_SESSION_TOKEN = "test-generation-session-token"
TEST_RISK_SNAPSHOT = RiskAssessmentSnapshot(
    rule_version="test-risk-v1",
    findings=(),
)


class StubAuthenticationService:
    """Fixed cookie identity used by generation-route tests."""

    def __init__(self, *, user_id: int = DEFAULT_TEST_USER_ID) -> None:
        self.user = AuthenticatedUser(user_id, "demo@example.com", False)

    async def register(self, *, email: object, password: object) -> IssuedSession:
        raise AssertionError((email, password))

    async def login(self, *, email: object, password: object) -> IssuedSession:
        raise AssertionError((email, password))

    async def get_current_user(
        self,
        raw_token: object,
    ) -> AuthenticatedUser | None:
        if raw_token != DEFAULT_TEST_SESSION_TOKEN:
            return None
        return self.user

    async def logout(self, raw_token: object) -> None:
        _ = raw_token


class StubModelService:
    """Deterministic model double used by all non-provider tests."""

    async def generate(
        self,
        image: ProcessedImage,
        *,
        product_name: str | None,
        target_audience: str | None,
        tone: str | None,
    ) -> GeneratedCopy:
        _ = product_name, target_audience, tone
        return GeneratedCopy(
            image_summary=(
                f"测试模型已识别 {image.image_format} 图片，"
                f"尺寸为 {image.width}×{image.height}。"
            ),
            title="测试生成标题",
            body="这是测试模型返回的小红书正文。",
            tags=("#接口测试", "#模型测试", "#结构化输出"),
        )


def build_test_app(
    *,
    model_service: GenerationModelService | None = None,
    generation_persistence: GenerationPersistence | None = None,
    auth_service: AuthenticationService | None = None,
    auth_rate_limiter: AuthRateLimiter | None = None,
    **overrides: Any,
) -> FastAPI:
    """Create the app without reading a developer's real .env file."""
    values: dict[str, Any] = {
        "SILICONFLOW_API_KEY": "test-secret-key",
        "SILICONFLOW_BASE_URL": "https://api.siliconflow.cn/v1",
        "VISION_MODEL_NAME": "qwen-test-model",
        "OCR_MODEL_NAME": "paddle-test-model",
        "CORS_ALLOW_ORIGINS": "http://localhost:5173",
        "DATABASE_ENABLED": False,
        "AUTH_ENABLED": False,
        "AUTH_COOKIE_SECURE": False,
    }
    values.update(overrides)
    settings = Settings(**values, _env_file=None)
    test_persistence = generation_persistence
    test_auth_service = auth_service
    if settings.auth_enabled:
        if test_persistence is None:
            test_persistence = NoOpGenerationPersistence()
        if test_auth_service is None:
            test_auth_service = StubAuthenticationService()
    return create_app(
        settings,
        model_service=model_service or StubModelService(),
        generation_persistence=test_persistence,
        auth_service=test_auth_service,
        auth_rate_limiter=auth_rate_limiter,
    )


async def send_request(
    method: str,
    url: str,
    *,
    application: FastAPI | None = None,
    raise_app_exceptions: bool = True,
    authenticated: bool = True,
    csrf: bool = True,
    **kwargs: Any,
) -> httpx.Response:
    """Exercise the ASGI app without starting a network server."""
    runtime_app = application or build_test_app()
    headers = httpx.Headers(kwargs.pop("headers", None))
    if (
        method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
        and csrf
        and "X-XHS-CSRF" not in headers
    ):
        headers["X-XHS-CSRF"] = "1"

    cookies = dict(kwargs.pop("cookies", None) or {})
    settings: Settings = runtime_app.state.settings
    if authenticated and settings.auth_enabled and "cookie" not in headers:
        cookies.setdefault(
            settings.auth_cookie_name,
            DEFAULT_TEST_SESSION_TOKEN,
        )

    transport = httpx.ASGITransport(
        app=runtime_app,
        raise_app_exceptions=raise_app_exceptions,
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        client.cookies.update(cookies)
        return await client.request(
            method,
            url,
            headers=headers,
            **kwargs,
        )


def make_image_bytes(
    image_format: str = "PNG",
    *,
    size: tuple[int, int] = (2, 2),
) -> bytes:
    """Create a tiny, genuinely decodable image for request tests."""
    output = BytesIO()
    with Image.new("RGB", size, color=(215, 45, 95)) as image:
        image.save(output, format=image_format)
    return output.getvalue()
