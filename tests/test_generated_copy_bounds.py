"""Boundary and fail-closed contracts for generated copy text fields."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from typing import Any

import pytest
from pydantic import ValidationError

from backend.schemas import BusinessException
from backend.services.image import ProcessedImage
from backend.services.model import GeneratedCopy, parse_generated_copy
from backend.services.model.qwen import ModelOutputError
from backend.services.persistence import (
    DeletedGeneration,
    FailedGeneration,
    PendingGeneration,
    StoredGeneration,
    StoredImagePreview,
    SuccessfulGeneration,
)
from backend.validation import (
    MAX_BODY_LENGTH,
    MAX_IMAGE_SUMMARY_LENGTH,
    MAX_TAG_LENGTH,
    validate_copy,
)
from tests.support import build_test_app, make_image_bytes, send_request


def _copy_payload(**updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "image_summary": "图片摘要",
        "title": "安全标题",
        "body": "正文内容",
        "tags": ["#标签一", "#标签二", "#标签三"],
    }
    payload.update(updates)
    return payload


@pytest.mark.parametrize(
    "field,value",
    [
        ("image_summary", "摘" * MAX_IMAGE_SUMMARY_LENGTH),
        ("body", "正" * MAX_BODY_LENGTH),
        ("tags", ["#" + "标" * (MAX_TAG_LENGTH - 1), "#二", "#三"]),
    ],
)
def test_generated_copy_accepts_each_exact_length_boundary(
    field: str,
    value: object,
) -> None:
    copy = GeneratedCopy.model_validate(_copy_payload(**{field: value}))

    if field == "tags":
        assert len(copy.tags[0]) == MAX_TAG_LENGTH
    else:
        assert len(getattr(copy, field)) == len(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field,value",
    [
        ("image_summary", "摘" * (MAX_IMAGE_SUMMARY_LENGTH + 1)),
        ("body", "正" * (MAX_BODY_LENGTH + 1)),
        ("tags", ["#" + "标" * MAX_TAG_LENGTH, "#二", "#三"]),
    ],
)
def test_generated_copy_rejects_each_boundary_plus_one(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        GeneratedCopy.model_validate(_copy_payload(**{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("image_summary", "摘" * (MAX_IMAGE_SUMMARY_LENGTH + 1)),
        ("body", "正" * (MAX_BODY_LENGTH + 1)),
        ("tags", ["#" + "标" * MAX_TAG_LENGTH, "#二", "#三"]),
    ],
)
def test_database_validation_rejects_each_boundary_plus_one(
    field: str,
    value: object,
) -> None:
    with pytest.raises(BusinessException):
        validate_copy(**_copy_payload(**{field: value}))


def test_database_validation_accepts_all_exact_boundaries_together() -> None:
    summary, title, body, tags = validate_copy(
        **_copy_payload(
            image_summary="摘" * MAX_IMAGE_SUMMARY_LENGTH,
            body="正" * MAX_BODY_LENGTH,
            tags=["#" + "标" * (MAX_TAG_LENGTH - 1), "#二", "#三"],
        )
    )

    assert len(summary) == MAX_IMAGE_SUMMARY_LENGTH
    assert title == "安全标题"
    assert len(body) == MAX_BODY_LENGTH
    assert len(tags[0]) == MAX_TAG_LENGTH


@pytest.mark.parametrize(
    "field,value,expected_detail",
    [
        (
            "image_summary",
            "摘" * (MAX_IMAGE_SUMMARY_LENGTH + 1),
            "field_length",
        ),
        ("body", "正" * (MAX_BODY_LENGTH + 1), "field_length"),
        (
            "tags",
            ["#" + "标" * MAX_TAG_LENGTH, "#二", "#三"],
            "invalid_tags",
        ),
    ],
)
def test_model_json_parser_rejects_overlong_fields_with_safe_diagnostics(
    field: str,
    value: object,
    expected_detail: str,
) -> None:
    content = json.dumps(_copy_payload(**{field: value}), ensure_ascii=False)

    with pytest.raises(ModelOutputError) as caught:
        parse_generated_copy(content)

    assert caught.value.reason == "format"
    assert caught.value.format_detail == "schema_validation"
    assert expected_detail in caught.value.schema_details
    assert str(value) not in str(caught.value)


@dataclass
class _RecordingPersistence:
    records: tuple[StoredGeneration, ...] = ()
    successes: list[SuccessfulGeneration] = field(default_factory=list)
    failures: list[FailedGeneration] = field(default_factory=list)

    async def create_pending(self, record: PendingGeneration) -> None:
        _ = record

    async def mark_success(self, record: SuccessfulGeneration) -> None:
        self.successes.append(record)

    async def mark_failed(self, record: FailedGeneration) -> None:
        self.failures.append(record)

    async def list_successful(
        self,
        *,
        user_id: int | None,
        limit: int,
    ) -> tuple[StoredGeneration, ...]:
        _ = user_id
        return self.records[:limit]

    async def get_successful(
        self,
        *,
        generation_id: str,
        user_id: int | None,
    ) -> StoredGeneration | None:
        _ = generation_id, user_id
        return None

    async def get_image_preview(
        self,
        *,
        generation_id: str,
        user_id: int | None,
    ) -> StoredImagePreview | None:
        _ = generation_id, user_id
        return None

    async def delete_successful(
        self,
        *,
        generation_id: str,
        user_id: int | None,
        deleted_at: datetime,
    ) -> DeletedGeneration | None:
        _ = generation_id, user_id, deleted_at
        return None


@dataclass
class _ConstructedInvalidModel:
    copy: GeneratedCopy

    async def generate(
        self,
        image: ProcessedImage,
        *,
        product_name: str | None,
        target_audience: str | None,
        tone: str | None,
    ) -> GeneratedCopy:
        _ = image, product_name, target_audience, tone
        return self.copy


@pytest.mark.parametrize(
    "field,value",
    [
        ("image_summary", "摘" * (MAX_IMAGE_SUMMARY_LENGTH + 1)),
        ("body", "正" * (MAX_BODY_LENGTH + 1)),
        ("tags", ("#" + "标" * MAX_TAG_LENGTH, "#二", "#三")),
    ],
)
def test_api_revalidates_model_instances_and_marks_invalid_output_failed(
    field: str,
    value: object,
) -> None:
    invalid = GeneratedCopy.model_construct(**_copy_payload(**{field: value}))
    persistence = _RecordingPersistence()

    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=build_test_app(
                model_service=_ConstructedInvalidModel(invalid),
                generation_persistence=persistence,
            ),
            files={"image": ("test.png", make_image_bytes(), "image/png")},
        )
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "MODEL_OUTPUT_INVALID"
    assert persistence.successes == []
    assert [record.error_code for record in persistence.failures] == [
        "MODEL_OUTPUT_INVALID"
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("image_summary", "摘" * (MAX_IMAGE_SUMMARY_LENGTH + 1)),
        ("body", "正" * (MAX_BODY_LENGTH + 1)),
        ("tags", ("#" + "标" * MAX_TAG_LENGTH, "#二", "#三")),
    ],
)
def test_history_api_fails_closed_on_overlong_adapter_data(
    field: str,
    value: object,
) -> None:
    copy_values = _copy_payload(**{field: value})
    persistence = _RecordingPersistence(
        records=(
            StoredGeneration(
                generation_id="00000000-0000-4000-8000-000000000001",
                user_id=None,
                image_summary=copy_values["image_summary"],
                title=copy_values["title"],
                body=copy_values["body"],
                tags=tuple(copy_values["tags"]),
                created_at=datetime.now(UTC),
            ),
        )
    )

    response = asyncio.run(
        send_request(
            "GET",
            "/api/v1/generations",
            application=build_test_app(generation_persistence=persistence),
        )
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "DATABASE_ERROR"
