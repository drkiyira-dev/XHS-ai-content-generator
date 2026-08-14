"""Isolated FastAPI application used only by the browser E2E suite.

It exercises the real HTTP, cookie, auth, image and SQLAlchemy paths while
keeping the developer's environment, MySQL data and model quota out of scope.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
import shutil
import tempfile

from anyio import CapacityLimiter, sleep
from fastapi import FastAPI
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.app import create_app
from backend.core.config import Settings
from backend.db.models import Base
from backend.services.auth import AuthRateLimiter, AuthService, SQLAlchemyAuthStore
from backend.services.image import ProcessedImage
from backend.services.model import GeneratedCopy
from backend.services.persistence import SQLAlchemyGenerationPersistence


class DelayedE2EModelService:
    """Deterministic model double with enough latency to observe the skeleton."""

    async def generate(
        self,
        image: ProcessedImage,
        *,
        product_name: str | None,
        target_audience: str | None,
        tone: str | None,
    ) -> GeneratedCopy:
        _ = image, product_name, target_audience, tone
        await sleep(0.8)
        return GeneratedCopy(
            image_summary="画面中可见一块红色矩形区域，用于浏览器端到端测试。",
            title="浏览器生成记录",
            body="这是一条稳定的测试初稿；请勿添加站外联系方式，微信号abc123。",
            tags=("#端到端测试", "#图片记录", "#内容初稿"),
        )


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def _build_app() -> FastAPI:
    temporary_root = Path(tempfile.mkdtemp(prefix="xhs-browser-e2e-"))
    engine = create_engine(
        f"sqlite+pysqlite:///{temporary_root / 'e2e.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 5},
        hide_parameters=True,
    )
    _enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    limiter = CapacityLimiter(2)

    settings = Settings(
        SILICONFLOW_API_KEY="e2e-placeholder-not-a-secret",
        SILICONFLOW_BASE_URL="https://api.siliconflow.cn/v1",
        VISION_MODEL_NAME="e2e-vision-stub",
        OCR_MODEL_NAME="e2e-ocr-stub",
        CORS_ALLOW_ORIGINS="http://127.0.0.1:15173",
        UPLOAD_DIR=temporary_root / "uploads",
        DATABASE_ENABLED=True,
        DATABASE_URL=None,
        DATABASE_TLS_CA=None,
        AUTH_ENABLED=True,
        AUTH_COOKIE_SECURE=False,
        _env_file=None,
    )
    application = create_app(
        settings,
        model_service=DelayedE2EModelService(),
        generation_persistence=SQLAlchemyGenerationPersistence(
            session_factory,
            limiter=limiter,
        ),
        auth_service=AuthService(
            SQLAlchemyAuthStore(session_factory, limiter=limiter),
        ),
        auth_rate_limiter=AuthRateLimiter(),
    )
    application_lifespan = application.router.lifespan_context

    @asynccontextmanager
    async def e2e_lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            async with application_lifespan(app):
                yield
        finally:
            engine.dispose()
            shutil.rmtree(temporary_root, ignore_errors=True)

    application.router.lifespan_context = e2e_lifespan
    return application


app = _build_app()
