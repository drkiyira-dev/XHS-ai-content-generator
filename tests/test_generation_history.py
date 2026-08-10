"""Tests for the value-add generation history API."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db import Base, GenerationRecord
from backend.services.persistence import (
    FailedGeneration,
    PendingGeneration,
    SQLAlchemyGenerationPersistence,
    SuccessfulGeneration,
)
from tests.support import build_test_app, send_request


def _build_adapter(
    tmp_path: Path,
    *,
    create_schema: bool = True,
) -> tuple[SQLAlchemyGenerationPersistence, object]:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'history.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    if create_schema:
        Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=Session,
    )
    return SQLAlchemyGenerationPersistence(factory), engine


async def _seed_history(adapter: SQLAlchemyGenerationPersistence) -> None:
    created_at = datetime(2026, 8, 10, 1, 0, tzinfo=UTC)
    for index in range(3):
        generation_id = str(uuid4())
        await adapter.create_pending(
            PendingGeneration(
                generation_id=generation_id,
                created_at=created_at + timedelta(minutes=index),
            )
        )
        await adapter.mark_success(
            SuccessfulGeneration(
                generation_id=generation_id,
                image_summary=f"第 {index + 1} 张图片摘要",
                title=f"历史标题{index + 1}",
                body=f"历史正文{index + 1}",
                tags=("#历史", "#生成", f"#版本{index + 1}"),
            )
        )

    failed_id = str(uuid4())
    await adapter.create_pending(
        PendingGeneration(
            generation_id=failed_id,
            created_at=created_at + timedelta(minutes=3),
        )
    )
    await adapter.mark_failed(
        FailedGeneration(
            generation_id=failed_id,
            error_code="MODEL_FAILED",
            failed_at=created_at + timedelta(minutes=4),
        )
    )


def test_history_returns_recent_successes_newest_first_and_honors_limit(
    tmp_path: Path,
) -> None:
    adapter, engine = _build_adapter(tmp_path)
    try:
        asyncio.run(_seed_history(adapter))
        application = build_test_app(generation_persistence=adapter)
        response = asyncio.run(
            send_request(
                "GET",
                "/api/v1/generations?limit=2",
                application=application,
            )
        )
    finally:
        engine.dispose()

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert [item["title"] for item in payload["items"]] == [
        "历史标题3",
        "历史标题2",
    ]
    assert payload["items"][0]["body"] == "历史正文3"
    assert payload["items"][0]["tags"] == ["#历史", "#生成", "#版本3"]
    assert datetime.fromisoformat(payload["items"][0]["created_at"]).utcoffset() == timedelta(0)
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("limit", [0, 51])
def test_history_rejects_limit_outside_the_documented_range(limit: int) -> None:
    response = asyncio.run(
        send_request("GET", f"/api/v1/generations?limit={limit}")
    )

    assert response.status_code == 422


def test_history_rejects_a_malformed_success_record_without_exposing_it(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, engine = _build_adapter(tmp_path)
    secret = "PRIVATE_HISTORY_SENTINEL"
    try:
        with Session(engine) as session:
            session.add(
                GenerationRecord(
                    task_id="not-a-generation-uuid",
                    status="success",
                    image_description=secret,
                    title="标题",
                    content="正文",
                    tags=secret,
                    created_at=datetime(2026, 8, 10, 1, 0),
                    updated_at=datetime(2026, 8, 10, 1, 0),
                )
            )
            session.commit()

        application = build_test_app(generation_persistence=adapter)
        response = asyncio.run(
            send_request(
                "GET",
                "/api/v1/generations",
                application=application,
            )
        )
    finally:
        engine.dispose()

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "DATABASE_ERROR"
    assert secret not in response.text
    assert secret not in caplog.text


def test_history_database_failure_uses_safe_error_without_sql_details(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, engine = _build_adapter(tmp_path, create_schema=False)
    try:
        application = build_test_app(generation_persistence=adapter)
        response = asyncio.run(
            send_request(
                "GET",
                "/api/v1/generations",
                application=application,
            )
        )
    finally:
        engine.dispose()

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "DATABASE_ERROR",
            "message": "历史记录暂时无法读取，请稍后重试。",
            "retryable": True,
        }
    }
    assert "stage=list_successful" in caplog.text
    assert "no such table" not in caplog.text
    assert "SELECT" not in caplog.text


def test_history_openapi_documents_limit_and_database_error() -> None:
    operation = build_test_app().openapi()["paths"]["/api/v1/generations"]["get"]

    limit = next(item for item in operation["parameters"] if item["name"] == "limit")
    assert limit["schema"]["minimum"] == 1
    assert limit["schema"]["maximum"] == 50
    assert operation["responses"]["500"]["content"]["application/json"][
        "schema"
    ]["$ref"].endswith("/ErrorResponse")
