"""Tests for B's database-independent generation persistence boundary."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from backend.api.errors import APIError
from backend.services.image import ProcessedImage
from backend.services.model import GeneratedCopy
from backend.services.persistence import (
    FailedGeneration,
    GenerationPersistenceError,
    PendingGeneration,
    SuccessfulGeneration,
)
from tests.support import build_test_app, make_image_bytes, send_request


@dataclass
class RecordingPersistence:
    """In-memory fake that records lifecycle calls without a database."""

    events: list[tuple[str, Any]]

    async def create_pending(self, record: PendingGeneration) -> None:
        self.events.append(("create_pending", record))

    async def mark_success(self, record: SuccessfulGeneration) -> None:
        self.events.append(("mark_success", record))

    async def mark_failed(self, record: FailedGeneration) -> None:
        self.events.append(("mark_failed", record))


@dataclass
class FailingMarkPersistence(RecordingPersistence):
    """Persistence fake whose failure update is unavailable."""

    async def mark_failed(self, record: FailedGeneration) -> None:
        await super().mark_failed(record)
        raise RuntimeError("private database failure details")


@dataclass
class FailingCreatePersistence(RecordingPersistence):
    """Persistence fake whose pending write reports a known storage failure."""

    async def create_pending(self, record: PendingGeneration) -> None:
        await super().create_pending(record)
        error = GenerationPersistenceError()
        error.add_note("private create_pending database details")
        raise error


@dataclass
class FailingSuccessPersistence(RecordingPersistence):
    """Persistence fake whose success update reports a known storage failure."""

    async def mark_success(self, record: SuccessfulGeneration) -> None:
        await super().mark_success(record)
        error = GenerationPersistenceError()
        error.add_note("private mark_success database details")
        raise error


@dataclass
class RecordingModelService:
    """Successful model fake sharing the lifecycle event list."""

    events: list[tuple[str, Any]]
    generated_copy: GeneratedCopy = field(
        default_factory=lambda: GeneratedCopy(
            image_summary="图片中可见一只白色帆布包。",
            title="白色帆布包",
            body="白色包身搭配简洁图案。",
            tags=("#帆布包", "#白色包袋", "#日常穿搭"),
        )
    )

    async def generate(
        self,
        image: ProcessedImage,
        *,
        product_name: str | None,
        target_audience: str | None,
        tone: str | None,
    ) -> GeneratedCopy:
        _ = image, product_name, target_audience, tone
        self.events.append(("model_generate", None))
        return self.generated_copy


@dataclass
class FailingModelService:
    """Model fake that raises one stable public API failure."""

    events: list[tuple[str, Any]]

    async def generate(
        self,
        image: ProcessedImage,
        *,
        product_name: str | None,
        target_audience: str | None,
        tone: str | None,
    ) -> GeneratedCopy:
        _ = image, product_name, target_audience, tone
        self.events.append(("model_generate", None))
        raise APIError(
            code="MODEL_FAILED",
            message="模型服务暂时不可用，请稍后重试。",
            status_code=502,
            retryable=True,
        )


def test_success_lifecycle_uses_one_generation_id_in_order() -> None:
    events: list[tuple[str, Any]] = []
    persistence = RecordingPersistence(events)
    application = build_test_app(
        model_service=RecordingModelService(events),
        generation_persistence=persistence,
    )

    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={"image": ("sample.jpg", make_image_bytes("JPEG"), "image/jpeg")},
        )
    )

    assert response.status_code == 200
    assert [name for name, _record in events] == [
        "create_pending",
        "model_generate",
        "mark_success",
    ]
    pending = events[0][1]
    success = events[2][1]
    assert isinstance(pending, PendingGeneration)
    assert isinstance(success, SuccessfulGeneration)
    assert pending.generation_id == success.generation_id
    assert pending.generation_id == response.json()["generation_id"]
    assert pending.created_at == datetime.fromisoformat(response.json()["created_at"])
    assert success.image_summary == "图片中可见一只白色帆布包。"
    assert success.tags == ("#帆布包", "#白色包袋", "#日常穿搭")


def test_model_failure_marks_same_generation_failed_and_preserves_error() -> None:
    events: list[tuple[str, Any]] = []
    persistence = RecordingPersistence(events)
    application = build_test_app(
        model_service=FailingModelService(events),
        generation_persistence=persistence,
    )

    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={"image": ("sample.png", make_image_bytes(), "image/png")},
        )
    )

    assert response.status_code == 502
    assert response.json()["error"] == {
        "code": "MODEL_FAILED",
        "message": "模型服务暂时不可用，请稍后重试。",
        "retryable": True,
    }
    assert [name for name, _record in events] == [
        "create_pending",
        "model_generate",
        "mark_failed",
    ]
    pending = events[0][1]
    failure = events[2][1]
    assert isinstance(pending, PendingGeneration)
    assert isinstance(failure, FailedGeneration)
    assert failure.generation_id == pending.generation_id
    assert failure.error_code == "MODEL_FAILED"
    assert failure.failed_at.tzinfo is not None


def test_mark_failed_storage_error_does_not_replace_primary_model_error(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    events: list[tuple[str, Any]] = []
    application = build_test_app(
        model_service=FailingModelService(events),
        generation_persistence=FailingMarkPersistence(events),
        UPLOAD_DIR=str(tmp_path),
    )

    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={"image": ("sample.png", make_image_bytes(), "image/png")},
        )
    )

    assert response.status_code == 502
    assert response.json()["error"] == {
        "code": "MODEL_FAILED",
        "message": "模型服务暂时不可用，请稍后重试。",
        "retryable": True,
    }
    assert [name for name, _record in events] == [
        "create_pending",
        "model_generate",
        "mark_failed",
    ]
    assert list(tmp_path.iterdir()) == []
    assert "RuntimeError" in caplog.text
    assert "private database failure details" not in caplog.text


def test_create_pending_failure_returns_safe_database_error_before_model_call(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    events: list[tuple[str, Any]] = []
    application = build_test_app(
        model_service=RecordingModelService(events),
        generation_persistence=FailingCreatePersistence(events),
        UPLOAD_DIR=str(tmp_path),
    )

    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={"image": ("sample.png", make_image_bytes(), "image/png")},
        )
    )

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "DATABASE_ERROR",
        "message": "生成结果暂时无法保存，请稍后重试。",
        "retryable": True,
    }
    assert [name for name, _record in events] == ["create_pending"]
    assert list(tmp_path.iterdir()) == []
    assert "stage=create_pending" in caplog.text
    assert "GenerationPersistenceError" in caplog.text
    assert "private create_pending database details" not in caplog.text


def test_mark_success_failure_returns_safe_database_error_after_model_call(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    events: list[tuple[str, Any]] = []
    application = build_test_app(
        model_service=RecordingModelService(events),
        generation_persistence=FailingSuccessPersistence(events),
        UPLOAD_DIR=str(tmp_path),
    )

    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={"image": ("sample.jpg", make_image_bytes("JPEG"), "image/jpeg")},
        )
    )

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "DATABASE_ERROR",
        "message": "生成结果暂时无法保存，请稍后重试。",
        "retryable": True,
    }
    assert [name for name, _record in events] == [
        "create_pending",
        "model_generate",
        "mark_success",
    ]
    assert list(tmp_path.iterdir()) == []
    assert "stage=mark_success" in caplog.text
    assert "GenerationPersistenceError" in caplog.text
    assert "private mark_success database details" not in caplog.text
