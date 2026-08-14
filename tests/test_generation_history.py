"""Tests for the value-add generation history API."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db import Base, GenerationRecord
from backend.services.auth import AuthenticatedUser, IssuedSession
from backend.services.persistence import (
    FailedGeneration,
    PendingGeneration,
    SQLAlchemyGenerationPersistence,
    SuccessfulGeneration,
)
from tests.support import (
    TEST_RISK_SNAPSHOT,
    build_test_app,
    make_image_bytes,
    send_request,
)


TEST_USER_ID = None
AUTH_DATABASE_URL = "mysql+pymysql://test:test@127.0.0.1/xhs_test"


class TokenAuthenticationService:
    """Resolve two fixed test tokens without accepting a request user ID."""

    def __init__(self) -> None:
        self.users = {
            "user-one-token": AuthenticatedUser(101, "one@example.com", False),
            "user-two-token": AuthenticatedUser(202, "two@example.com", False),
        }

    async def register(self, *, email: object, password: object) -> IssuedSession:
        raise AssertionError((email, password))

    async def login(self, *, email: object, password: object) -> IssuedSession:
        raise AssertionError((email, password))

    async def get_current_user(
        self,
        raw_token: object,
    ) -> AuthenticatedUser | None:
        return self.users.get(raw_token) if isinstance(raw_token, str) else None

    async def logout(self, raw_token: object) -> None:
        _ = raw_token


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
                user_id=TEST_USER_ID,
                created_at=created_at + timedelta(minutes=index),
            )
        )
        await adapter.mark_success(
            SuccessfulGeneration(
                generation_id=generation_id,
                user_id=TEST_USER_ID,
                image_summary=f"第 {index + 1} 张图片摘要",
                title=f"历史标题{index + 1}",
                body=f"历史正文{index + 1}",
                tags=("#历史", "#生成", f"#版本{index + 1}"),
                risk_assessment=TEST_RISK_SNAPSHOT,
            )
        )

    failed_id = str(uuid4())
    await adapter.create_pending(
        PendingGeneration(
            generation_id=failed_id,
            user_id=TEST_USER_ID,
            created_at=created_at + timedelta(minutes=3),
        )
    )
    await adapter.mark_failed(
        FailedGeneration(
            generation_id=failed_id,
            user_id=TEST_USER_ID,
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
    assert (
        datetime.fromisoformat(payload["items"][0]["created_at"]).utcoffset()
        == timedelta(0)
    )
    assert response.headers["cache-control"] == "no-store"


def test_legacy_history_returns_only_the_explicit_null_owner_partition(
    tmp_path: Path,
) -> None:
    adapter, engine = _build_adapter(tmp_path)
    created_at = datetime(2026, 8, 10, 1, 0, tzinfo=UTC)

    async def seed_mixed_owners() -> None:
        for generation_id, user_id, title in (
            (str(uuid4()), None, "匿名历史"),
            (str(uuid4()), 202, "账号历史"),
        ):
            await adapter.create_pending(
                PendingGeneration(generation_id, user_id, created_at)
            )
            await adapter.mark_success(
                SuccessfulGeneration(
                    generation_id=generation_id,
                    user_id=user_id,
                    image_summary="测试图片摘要",
                    title=title,
                    body="测试历史正文",
                    tags=("#历史", "#归属", "#隔离"),
                    risk_assessment=TEST_RISK_SNAPSHOT,
                )
            )

    try:
        asyncio.run(seed_mixed_owners())
        response = asyncio.run(
            send_request(
                "GET",
                "/api/v1/generations",
                application=build_test_app(generation_persistence=adapter),
            )
        )
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert [item["title"] for item in response.json()["items"]] == ["匿名历史"]


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
                    user_id=TEST_USER_ID,
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
    assert all(parameter["name"] != "user_id" for parameter in operation["parameters"])
    assert "user_id" not in str(operation)


def test_authenticated_generation_routes_isolate_users_and_legacy_history(
    tmp_path: Path,
) -> None:
    adapter, engine = _build_adapter(tmp_path)
    auth_service = TokenAuthenticationService()
    legacy_id = str(uuid4())
    user_two_id = str(uuid4())
    created_at = datetime(2026, 8, 10, 1, 0, tzinfo=UTC)

    async def seed_other_partitions() -> None:
        for generation_id, user_id, title in (
            (legacy_id, None, "旧记录"),
            (user_two_id, 202, "用户二记录"),
        ):
            await adapter.create_pending(
                PendingGeneration(generation_id, user_id, created_at)
            )
            await adapter.mark_success(
                SuccessfulGeneration(
                    generation_id=generation_id,
                    user_id=user_id,
                    image_summary="测试图片摘要",
                    title=title,
                    body="测试历史正文",
                    tags=("#历史", "#归属", "#隔离"),
                    risk_assessment=TEST_RISK_SNAPSHOT,
                )
            )

    application = build_test_app(
        generation_persistence=adapter,
        auth_service=auth_service,
        DATABASE_ENABLED=True,
        DATABASE_URL=AUTH_DATABASE_URL,
        AUTH_ENABLED=True,
        UPLOAD_DIR=str(tmp_path / "uploads"),
    )
    try:
        asyncio.run(seed_other_partitions())
        created = asyncio.run(
            send_request(
                "POST",
                "/api/v1/generations",
                application=application,
                cookies={"xhs_session_local": "user-one-token"},
                files={
                    "image": (
                        "owned.png",
                        make_image_bytes(),
                        "image/png",
                    )
                },
            )
        )
        user_one_history = asyncio.run(
            send_request(
                "GET",
                "/api/v1/generations",
                application=application,
                cookies={"xhs_session_local": "user-one-token"},
            )
        )
        user_two_history = asyncio.run(
            send_request(
                "GET",
                "/api/v1/generations",
                application=application,
                cookies={"xhs_session_local": "user-two-token"},
            )
        )
    finally:
        engine.dispose()

    assert created.status_code == 200
    assert user_one_history.status_code == 200
    assert [item["title"] for item in user_one_history.json()["items"]] == [
        "测试生成标题"
    ]
    assert user_two_history.status_code == 200
    assert [item["title"] for item in user_two_history.json()["items"]] == [
        "用户二记录"
    ]
