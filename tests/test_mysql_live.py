"""Explicitly gated end-to-end checks against the dedicated local MySQL DB."""

import asyncio
from datetime import UTC, datetime
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

import backend.api.v1.generations as generations_module
from backend.api.errors import APIError
from backend.app import create_app
from backend.core.config import ENV_FILE, Settings
from backend.db import GenerationRecord
from backend.services.image import ProcessedImage
from backend.services.model import GeneratedCopy
from backend.services.persistence import (
    FailedGeneration,
    GenerationPersistenceError,
    PendingGeneration,
    SuccessfulGeneration,
)
from backend.services.persistence.runtime import (
    create_sqlalchemy_persistence_runtime,
)
from tests.support import (
    TEST_RISK_SNAPSHOT,
    StubModelService,
    make_image_bytes,
    send_request,
)


LIVE_CONFIRM_NAME = "XHS_MYSQL_LIVE_TEST_CONFIRM"
LIVE_CONFIRM_VALUE = "YES_USE_XHS_AI_TEST"
EXPECTED_DATABASE = "xhs_ai_test"
EXPECTED_USER = "xhs_app"
EXPECTED_HOST = "127.0.0.1"
EXPECTED_PORT = 3306


class FailingModelService:
    """Stable model failure used to verify MySQL's failed terminal state."""

    async def generate(
        self,
        image: ProcessedImage,
        *,
        product_name: str | None,
        target_audience: str | None,
        tone: str | None,
    ) -> GeneratedCopy:
        _ = image, product_name, target_audience, tone
        raise APIError(
            code="MODEL_FAILED",
            message="模型服务暂时不可用，请稍后重试。",
            status_code=502,
            retryable=True,
        )


pytestmark = pytest.mark.mysql_live


def test_real_mysql_generation_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if os.environ.get("CI", "").casefold() == "true":
        pytest.skip("live MySQL test is disabled in CI")
    if os.environ.get(LIVE_CONFIRM_NAME) != LIVE_CONFIRM_VALUE:
        pytest.skip("live MySQL test requires an explicit confirmation variable")

    settings = Settings(
        UPLOAD_DIR=str(tmp_path / "uploads"),
        _env_file=ENV_FILE,
    )
    assert settings.database_enabled is True
    assert settings.auth_enabled is False
    database_url = _load_expected_database_url(settings)

    engine = create_engine(
        database_url,
        connect_args={
            "charset": "utf8mb4",
            "connect_timeout": 5,
            "read_timeout": 10,
            "write_timeout": 10,
            "local_infile": False,
        },
        echo=False,
        echo_pool=False,
        hide_parameters=True,
        pool_pre_ping=True,
    )
    factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=Session,
    )
    success_id = uuid4()
    failed_id = uuid4()
    race_id = uuid4()
    created_ids = (str(success_id), str(failed_id), str(race_id))

    try:
        success_response = _send_generation(
            settings,
            StubModelService(),
            success_id,
            monkeypatch,
        )
        assert success_response.status_code == 200
        assert success_response.json()["generation_id"] == str(success_id)

        success_record = _load_record(factory, str(success_id))
        assert success_record is not None
        assert success_record.user_id is None
        assert success_record.status == "success"
        assert success_record.title == success_response.json()["title"]
        assert success_record.content == success_response.json()["body"]
        assert success_record.tags == success_response.json()["tags"]

        failed_response = _send_generation(
            settings,
            FailingModelService(),
            failed_id,
            monkeypatch,
        )
        assert failed_response.status_code == 502
        assert failed_response.json()["error"]["code"] == "MODEL_FAILED"

        failed_record = _load_record(factory, str(failed_id))
        assert failed_record is not None
        assert failed_record.user_id is None
        assert failed_record.status == "failed"
        assert failed_record.error_code == "MODEL_FAILED"
        assert failed_record.error_message is None

        duplicate_response = _send_generation(
            settings,
            StubModelService(),
            success_id,
            monkeypatch,
        )
        assert duplicate_response.status_code == 500
        assert duplicate_response.json()["error"]["code"] == "DATABASE_ERROR"
        duplicate_record = _load_record(factory, str(success_id))
        assert duplicate_record is not None
        assert duplicate_record.status == "success"

        race_status = asyncio.run(_exercise_terminal_race(settings, race_id))
        assert race_status in {"success", "failed"}
        race_record = _load_record(factory, str(race_id))
        assert race_record is not None
        assert race_record.user_id is None
        assert race_record.status == race_status
        if race_status == "success":
            assert race_record.title == "MySQL并发测试"
            assert race_record.content == "用于验证 pending 只能进入一个终态。"
            assert race_record.tags == ["#MySQL测试", "#并发测试", "#状态测试"]
            assert race_record.error_code is None
        else:
            assert race_record.title is None
            assert race_record.content is None
            assert race_record.tags is None
            assert race_record.error_code == "MODEL_FAILED"

        with factory() as session:
            record_count = session.scalar(
                select(func.count())
                .select_from(GenerationRecord)
                .where(GenerationRecord.task_id.in_(created_ids))
            )
        assert record_count == 3
        upload_dir = settings.resolved_upload_dir
        assert not upload_dir.exists() or not any(upload_dir.iterdir())

        engine.dispose()
        reconnected_engine = create_engine(
            database_url,
            connect_args={
                "charset": "utf8mb4",
                "connect_timeout": 5,
                "read_timeout": 10,
                "write_timeout": 10,
                "local_infile": False,
            },
            echo=False,
            hide_parameters=True,
            pool_pre_ping=True,
        )
        try:
            reconnected_factory = sessionmaker(
                bind=reconnected_engine,
                expire_on_commit=False,
                class_=Session,
            )
            assert _load_record(reconnected_factory, str(success_id)) is not None
            assert _load_record(reconnected_factory, str(failed_id)) is not None
            assert _load_record(reconnected_factory, str(race_id)) is not None
        finally:
            reconnected_engine.dispose()
    finally:
        try:
            with engine.begin() as connection:
                connection.execute(
                    delete(GenerationRecord).where(
                        GenerationRecord.task_id.in_(created_ids)
                    )
                )
        finally:
            engine.dispose()

    try:
        with factory() as session:
            remaining = session.execute(
                select(GenerationRecord.task_id).where(
                    GenerationRecord.task_id.in_(created_ids)
                )
            ).all()
        assert remaining == []
    finally:
        engine.dispose()


def _send_generation(
    settings: Settings,
    model_service: object,
    generation_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(generations_module, "uuid4", lambda: generation_id)
    application = create_app(settings, model_service=model_service)

    async def send():
        async with application.router.lifespan_context(application):
            return await send_request(
                "POST",
                "/api/v1/generations",
                application=application,
                files={
                    "image": (
                        "mysql-live.png",
                        make_image_bytes(),
                        "image/png",
                    )
                },
            )

    return asyncio.run(send())


async def _exercise_terminal_race(
    settings: Settings,
    generation_id: UUID,
) -> str:
    runtime = create_sqlalchemy_persistence_runtime(settings)
    try:
        await runtime.startup()
        persistence = runtime.persistence
        await persistence.create_pending(
            PendingGeneration(
                generation_id=str(generation_id),
                user_id=None,
                created_at=datetime.now(UTC),
            )
        )
        success = SuccessfulGeneration(
            generation_id=str(generation_id),
            user_id=None,
            image_summary="MySQL 并发测试图片",
            title="MySQL并发测试",
            body="用于验证 pending 只能进入一个终态。",
            tags=("#MySQL测试", "#并发测试", "#状态测试"),
            risk_assessment=TEST_RISK_SNAPSHOT,
        )
        failed = FailedGeneration(
            generation_id=str(generation_id),
            user_id=None,
            error_code="MODEL_FAILED",
            failed_at=datetime.now(UTC),
        )
        results = await asyncio.gather(
            persistence.mark_success(success),
            persistence.mark_failed(failed),
            return_exceptions=True,
        )
    finally:
        await runtime.aclose()

    assert sum(result is None for result in results) == 1
    errors = [result for result in results if result is not None]
    assert len(errors) == 1
    assert isinstance(errors[0], GenerationPersistenceError)
    return "success" if results[0] is None else "failed"


def _load_record(
    factory: sessionmaker[Session],
    generation_id: str,
) -> GenerationRecord | None:
    with factory() as session:
        record = session.execute(
            select(GenerationRecord).where(
                GenerationRecord.task_id == generation_id
            )
        ).scalar_one_or_none()
        if record is not None:
            session.expunge(record)
        return record


def _load_expected_database_url(settings: Settings) -> URL:
    database_url: URL | None = None
    try:
        if settings.database_url is not None:
            candidate = make_url(settings.database_url.get_secret_value())
            port = (
                candidate.port
                if candidate.port is not None
                else EXPECTED_PORT
            )
            if (
                candidate.drivername == "mysql+pymysql"
                and candidate.database == EXPECTED_DATABASE
                and candidate.username == EXPECTED_USER
                and candidate.host == EXPECTED_HOST
                and port == EXPECTED_PORT
                and candidate.password is not None
                and bool(candidate.password.strip())
                and not candidate.query
            ):
                database_url = candidate
    except Exception:
        database_url = None

    if database_url is None:
        pytest.fail("live MySQL configuration does not match the dedicated target")
    return database_url
