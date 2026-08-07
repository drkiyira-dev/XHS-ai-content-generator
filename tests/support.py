"""Reusable app and image factories for backend tests."""

from io import BytesIO
from typing import Any

from fastapi import FastAPI
import httpx
from PIL import Image

from backend.app import create_app
from backend.core.config import Settings
from backend.services.image import ProcessedImage
from backend.services.model import GeneratedCopy, GenerationModelService


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
    **overrides: Any,
) -> FastAPI:
    """Create the app without reading a developer's real .env file."""
    values: dict[str, Any] = {
        "SILICONFLOW_API_KEY": "test-secret-key",
        "SILICONFLOW_BASE_URL": "https://api.siliconflow.cn/v1",
        "VISION_MODEL_NAME": "qwen-test-model",
        "OCR_MODEL_NAME": "paddle-test-model",
        "CORS_ALLOW_ORIGINS": "http://localhost:5173",
    }
    values.update(overrides)
    settings = Settings(**values, _env_file=None)
    return create_app(
        settings,
        model_service=model_service or StubModelService(),
    )


async def send_request(
    method: str,
    url: str,
    *,
    application: FastAPI | None = None,
    raise_app_exceptions: bool = True,
    **kwargs: Any,
) -> httpx.Response:
    """Exercise the ASGI app without starting a network server."""
    transport = httpx.ASGITransport(
        app=application or build_test_app(),
        raise_app_exceptions=raise_app_exceptions,
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.request(method, url, **kwargs)


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
