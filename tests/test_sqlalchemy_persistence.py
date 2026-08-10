"""Integration tests for B's async adapter over member C's database core."""

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
import time
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import backend.api.v1.generations as generations_module
import backend.services.persistence.sqlalchemy as persistence_module
from backend.api.errors import APIError
from backend.db import Base, GenerationRecord
from backend.services.image import ProcessedImage
from backend.services.model import GeneratedCopy
from backend.services.persistence import (
    GenerationPersistenceError,
    PendingGeneration,
    SQLAlchemyGenerationPersistence,
    SuccessfulGeneration,
)
from tests.support import build_test_app, make_image_bytes, send_request


class FailingModelService:
    """Model double that exercises the persisted failure lifecycle."""

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


@contextmanager
def persistence_database(
    tmp_path: Path,
    *,
    clock: Callable[[], datetime] | None = None,
) -> Iterator[
    tuple[SQLAlchemyGenerationPersistence, sessionmaker[Session]]
]:
    """Build a thread-safe file SQLite adapter without reading .env."""
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'adapter-tests.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=Session,
    )
    adapter = (
        SQLAlchemyGenerationPersistence(factory)
        if clock is None
        else SQLAlchemyGenerationPersistence(factory, clock=clock)
    )
    try:
        yield adapter, factory
    finally:
        engine.dispose()


def load_records(factory: sessionmaker[Session]) -> list[GenerationRecord]:
    with factory() as session:
        records = list(
            session.execute(
                select(GenerationRecord).order_by(GenerationRecord.id)
            ).scalars()
        )
        for record in records:
            session.expunge(record)
        return records


def test_fastapi_success_persists_the_same_response_generation_id(
    tmp_path: Path,
) -> None:
    with persistence_database(tmp_path) as (adapter, factory):
        application = build_test_app(
            generation_persistence=adapter,
            UPLOAD_DIR=str(tmp_path / "uploads"),
        )
        response = asyncio.run(
            send_request(
                "POST",
                "/api/v1/generations",
                application=application,
                files={
                    "image": (
                        "sample.jpg",
                        make_image_bytes("JPEG"),
                        "image/jpeg",
                    )
                },
            )
        )
        records = load_records(factory)

    assert response.status_code == 200
    assert len(records) == 1
    record = records[0]
    payload = response.json()
    assert record.task_id == payload["generation_id"]
    assert record.status == "success"
    assert record.image_description == payload["image_summary"]
    assert record.title == payload["title"]
    assert record.content == payload["body"]
    assert record.tags == payload["tags"]


def test_fastapi_model_failure_marks_the_same_pending_record_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_generation_id = uuid4()
    monkeypatch.setattr(
        generations_module,
        "uuid4",
        lambda: fixed_generation_id,
    )

    with persistence_database(tmp_path) as (adapter, factory):
        application = build_test_app(
            model_service=FailingModelService(),
            generation_persistence=adapter,
            UPLOAD_DIR=str(tmp_path / "uploads"),
        )
        response = asyncio.run(
            send_request(
                "POST",
                "/api/v1/generations",
                application=application,
                files={
                    "image": (
                        "sample.png",
                        make_image_bytes(),
                        "image/png",
                    )
                },
            )
        )
        records = load_records(factory)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "MODEL_FAILED"
    assert len(records) == 1
    assert records[0].task_id == str(fixed_generation_id)
    assert records[0].status == "failed"
    assert records[0].error_code == "MODEL_FAILED"
    assert records[0].error_message is None


def test_adapter_rolls_back_invalid_copy_and_returns_one_sanitized_error(
    tmp_path: Path,
) -> None:
    generation_id = str(uuid4())
    created_at = datetime.now(UTC)
    pending = PendingGeneration(generation_id, created_at)
    invalid = SuccessfulGeneration(
        generation_id=generation_id,
        image_summary="图片描述",
        title=(
            "这是一个明显超过二十个汉字"
            "所以不能写入数据库的标题"
        ),
        body="正文内容",
        tags=("#一", "#二", "#三"),
    )

    with persistence_database(tmp_path) as (adapter, factory):
        asyncio.run(adapter.create_pending(pending))
        with pytest.raises(GenerationPersistenceError) as caught:
            asyncio.run(adapter.mark_success(invalid))
        records = load_records(factory)

    error = caught.value
    assert str(error) == "generation persistence failed"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert len(records) == 1
    assert records[0].status == "pending"
    assert records[0].title is None


def test_duplicate_pending_error_has_no_sqlalchemy_context_or_details(
    tmp_path: Path,
) -> None:
    generation_id = str(uuid4())
    pending = PendingGeneration(generation_id, datetime.now(UTC))

    with persistence_database(tmp_path) as (adapter, factory):
        asyncio.run(adapter.create_pending(pending))
        with pytest.raises(GenerationPersistenceError) as caught:
            asyncio.run(adapter.create_pending(pending))
        records = load_records(factory)

    error = caught.value
    assert str(error) == "generation persistence failed"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert len(records) == 1
    assert records[0].task_id == generation_id


def test_adapter_uses_injected_clock_and_stores_it_as_utc(
    tmp_path: Path,
) -> None:
    generation_id = str(uuid4())
    created_at = datetime.now(UTC)
    completed_at_jst = datetime(
        2026,
        8,
        9,
        15,
        30,
        tzinfo=timezone(timedelta(hours=9)),
    )
    pending = PendingGeneration(generation_id, created_at)
    success = SuccessfulGeneration(
        generation_id=generation_id,
        image_summary="图片描述",
        title="标题",
        body="正文内容",
        tags=("#一", "#二", "#三"),
    )

    with persistence_database(
        tmp_path,
        clock=lambda: completed_at_jst,
    ) as (adapter, factory):
        asyncio.run(adapter.create_pending(pending))
        asyncio.run(adapter.mark_success(success))
        records = load_records(factory)

    assert len(records) == 1
    assert records[0].updated_at == datetime(2026, 8, 9, 6, 30)


def test_fastapi_sql_error_returns_fixed_database_error_without_sql_details(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'missing-table.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=Session,
    )
    adapter = SQLAlchemyGenerationPersistence(factory)
    application = build_test_app(
        generation_persistence=adapter,
        UPLOAD_DIR=str(tmp_path / "uploads"),
    )
    try:
        response = asyncio.run(
            send_request(
                "POST",
                "/api/v1/generations",
                application=application,
                files={
                    "image": (
                        "sample.png",
                        make_image_bytes(),
                        "image/png",
                    )
                },
            )
        )
    finally:
        engine.dispose()

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "DATABASE_ERROR",
        "message": "生成结果暂时无法保存，请稍后重试。",
        "retryable": True,
    }
    assert "stage=create_pending" in caplog.text
    assert "GenerationPersistenceError" in caplog.text
    assert "no such table" not in caplog.text
    assert "INSERT INTO" not in caplog.text


def test_adapter_database_work_does_not_block_the_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation_id = str(uuid4())
    pending = PendingGeneration(generation_id, datetime.now(UTC))
    original_create_pending = persistence_module.create_pending_record

    def slow_create_pending(session: Session, **kwargs: object) -> object:
        time.sleep(0.15)
        return original_create_pending(session, **kwargs)

    monkeypatch.setattr(
        persistence_module,
        "create_pending_record",
        slow_create_pending,
    )

    with persistence_database(tmp_path) as (adapter, factory):

        async def exercise_adapter() -> bool:
            task = asyncio.create_task(adapter.create_pending(pending))
            await asyncio.sleep(0.02)
            completed_before_sleep_finished = task.done()
            await task
            return completed_before_sleep_finished

        blocked = asyncio.run(exercise_adapter())
        records = load_records(factory)

    assert blocked is False
    assert len(records) == 1
    assert records[0].task_id == generation_id
