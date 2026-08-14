"""Security regressions for private history image previews and deletion."""

import asyncio
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image, PngImagePlugin

from backend.services.image import (
    HistoryPreviewError,
    PREVIEW_MAX_BYTES,
    ProcessedImage,
    create_history_image_preview,
)
from backend.services.persistence import (
    DeletedGeneration,
    FailedGeneration,
    PendingGeneration,
    StoredGeneration,
    StoredImagePreview,
    SuccessfulGeneration,
)
from tests.support import (
    TEST_RISK_SNAPSHOT,
    StubAuthenticationService,
    build_test_app,
    make_image_bytes,
    send_request,
)


AUTH_DATABASE_URL = "mysql+pymysql://test:test@127.0.0.1/xhs_test"


class MemoryPreviewPersistence:
    """Small owner-aware double that never touches a real database."""

    def __init__(self) -> None:
        self.pending: dict[str, PendingGeneration] = {}
        self.successful: dict[str, SuccessfulGeneration] = {}
        self.failed: dict[str, FailedGeneration] = {}
        self.deleted: set[str] = set()

    async def create_pending(self, record: PendingGeneration) -> None:
        self.pending[record.generation_id] = record

    async def mark_success(self, record: SuccessfulGeneration) -> None:
        self.successful[record.generation_id] = record

    async def mark_failed(self, record: FailedGeneration) -> None:
        self.failed[record.generation_id] = record

    async def list_successful(
        self,
        *,
        user_id: int | None,
        limit: int,
    ) -> tuple[StoredGeneration, ...]:
        records: list[StoredGeneration] = []
        for generation_id, record in reversed(tuple(self.successful.items())):
            if record.user_id != user_id or generation_id in self.deleted:
                continue
            records.append(self._stored(record))
        return tuple(records[:limit])

    async def get_successful(
        self,
        *,
        generation_id: str,
        user_id: int | None,
    ) -> StoredGeneration | None:
        record = self.successful.get(generation_id)
        if (
            record is None
            or record.user_id != user_id
            or generation_id in self.deleted
        ):
            return None
        return self._stored(record)

    async def get_image_preview(
        self,
        *,
        generation_id: str,
        user_id: int | None,
    ) -> StoredImagePreview | None:
        record = self.successful.get(generation_id)
        if (
            record is None
            or record.user_id != user_id
            or generation_id in self.deleted
            or record.image_preview is None
            or record.image_preview_media_type is None
        ):
            return None
        return StoredImagePreview(
            generation_id=generation_id,
            user_id=user_id,
            content=record.image_preview,
            media_type=record.image_preview_media_type,
        )

    async def delete_successful(
        self,
        *,
        generation_id: str,
        user_id: int | None,
        deleted_at: datetime,
    ) -> DeletedGeneration | None:
        assert deleted_at.tzinfo is not None
        record = self.successful.get(generation_id)
        if record is None or record.user_id != user_id:
            return None
        deleted_now = generation_id not in self.deleted
        self.deleted.add(generation_id)
        return DeletedGeneration(generation_id, user_id, deleted_now)

    def _stored(self, record: SuccessfulGeneration) -> StoredGeneration:
        pending = self.pending[record.generation_id]
        return StoredGeneration(
            generation_id=record.generation_id,
            user_id=record.user_id,
            image_summary=record.image_summary,
            title=record.title,
            body=record.body,
            tags=record.tags,
            created_at=pending.created_at,
            risk_assessment=record.risk_assessment,
            has_image_preview=record.image_preview is not None,
        )


def _processed_png(path: Path, *, size: tuple[int, int] = (1200, 700)) -> ProcessedImage:
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private-note", "MUST_NOT_SURVIVE")
    with Image.new("RGB", size, color=(37, 88, 131)) as source:
        source.save(path, format="PNG", pnginfo=metadata)
    return ProcessedImage(
        path=path,
        image_format="PNG",
        mime_type="image/png",
        width=size[0],
        height=size[1],
        exif_transposed=False,
        alpha_composited=False,
        resized=False,
    )


def _auth_app(
    persistence: MemoryPreviewPersistence,
    *,
    user_id: int,
):
    return build_test_app(
        generation_persistence=persistence,
        auth_service=StubAuthenticationService(user_id=user_id),
        DATABASE_ENABLED=True,
        DATABASE_URL=AUTH_DATABASE_URL,
        AUTH_ENABLED=True,
    )


def test_preview_is_metadata_free_bounded_rgb_webp(tmp_path: Path) -> None:
    preview = asyncio.run(
        create_history_image_preview(_processed_png(tmp_path / "processed.png"))
    )

    assert preview.media_type == "image/webp"
    assert 0 < len(preview.content) <= PREVIEW_MAX_BYTES
    assert max(preview.width, preview.height) <= 480
    assert b"MUST_NOT_SURVIVE" not in preview.content
    assert "RIFF" not in repr(preview)
    with Image.open(BytesIO(preview.content)) as decoded:
        decoded.load()
        assert decoded.format == "WEBP"
        assert decoded.mode == "RGB"
        assert decoded.size == (preview.width, preview.height)
        assert "private-note" not in decoded.info


def test_preview_failure_never_exposes_its_private_path(tmp_path: Path) -> None:
    secret_path = tmp_path / "PRIVATE_UPLOAD_NAME.png"
    secret_path.write_bytes(b"not an image")
    processed = ProcessedImage(
        path=secret_path,
        image_format="PNG",
        mime_type="image/png",
        width=1,
        height=1,
        exif_transposed=False,
        alpha_composited=False,
        resized=False,
    )

    with pytest.raises(HistoryPreviewError) as captured:
        asyncio.run(create_history_image_preview(processed))

    assert str(secret_path) not in str(captured.value)
    assert secret_path.name not in str(captured.value)


def test_created_preview_is_listed_fetched_and_soft_deleted() -> None:
    persistence = MemoryPreviewPersistence()
    application = build_test_app(generation_persistence=persistence)
    created = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={"image": ("sample.png", make_image_bytes(size=(900, 600)), "image/png")},
        )
    )
    generation_id = created.json()["generation_id"]

    history = asyncio.run(
        send_request("GET", "/api/v1/generations", application=application)
    )
    item = history.json()["items"][0]
    preview = asyncio.run(
        send_request(
            "GET",
            f"/api/v1/generations/{generation_id}/image-preview",
            application=application,
        )
    )
    deleted = asyncio.run(
        send_request(
            "DELETE",
            f"/api/v1/generations/{generation_id}",
            application=application,
        )
    )
    deleted_again = asyncio.run(
        send_request(
            "DELETE",
            f"/api/v1/generations/{generation_id}",
            application=application,
        )
    )
    preview_after_delete = asyncio.run(
        send_request(
            "GET",
            f"/api/v1/generations/{generation_id}/image-preview",
            application=application,
        )
    )

    assert created.status_code == 200
    assert item["has_image_preview"] is True
    assert item["image_preview_url"] == (
        f"/api/v1/generations/{generation_id}/image-preview"
    )
    assert history.headers["cache-control"] == "no-store"
    assert "Cookie" in history.headers["vary"]
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("image/webp")
    assert preview.headers["cache-control"] == "no-store"
    assert preview.headers["x-content-type-options"] == "nosniff"
    assert "Cookie" in preview.headers["vary"]
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert deleted_again.status_code == 204
    assert preview_after_delete.status_code == 404
    assert preview_after_delete.json()["error"]["code"] == "GENERATION_NOT_FOUND"
    assert preview_after_delete.headers["cache-control"] == "no-store"
    assert "Cookie" in preview_after_delete.headers["vary"]


def test_preview_and_delete_are_owner_scoped_and_delete_requires_csrf() -> None:
    persistence = MemoryPreviewPersistence()
    generation_id = str(uuid4())
    created_at = datetime(2026, 8, 12, tzinfo=UTC)
    persistence.pending[generation_id] = PendingGeneration(
        generation_id,
        101,
        created_at,
    )
    persistence.successful[generation_id] = SuccessfulGeneration(
        generation_id=generation_id,
        user_id=101,
        image_summary="安全摘要",
        title="安全标题",
        body="安全正文",
        tags=("#安全", "#归属", "#隔离"),
        risk_assessment=TEST_RISK_SNAPSHOT,
        image_preview=make_image_bytes("JPEG"),
        image_preview_media_type="image/jpeg",
    )
    owner_app = _auth_app(persistence, user_id=101)
    other_app = _auth_app(persistence, user_id=202)

    owner_preview = asyncio.run(
        send_request(
            "GET",
            f"/api/v1/generations/{generation_id}/image-preview",
            application=owner_app,
        )
    )
    other_preview = asyncio.run(
        send_request(
            "GET",
            f"/api/v1/generations/{generation_id}/image-preview",
            application=other_app,
        )
    )
    anonymous_preview = asyncio.run(
        send_request(
            "GET",
            f"/api/v1/generations/{generation_id}/image-preview",
            application=owner_app,
            authenticated=False,
        )
    )
    missing_csrf = asyncio.run(
        send_request(
            "DELETE",
            f"/api/v1/generations/{generation_id}",
            application=owner_app,
            csrf=False,
        )
    )
    other_delete = asyncio.run(
        send_request(
            "DELETE",
            f"/api/v1/generations/{generation_id}",
            application=other_app,
            headers={"X-XHS-CSRF": "1"},
        )
    )
    owner_delete = asyncio.run(
        send_request(
            "DELETE",
            f"/api/v1/generations/{generation_id}",
            application=owner_app,
            headers={"X-XHS-CSRF": "1"},
        )
    )

    assert owner_preview.status_code == 200
    assert other_preview.status_code == 404
    assert anonymous_preview.status_code == 401
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "CSRF_REJECTED"
    assert other_delete.status_code == 404
    assert owner_delete.status_code == 204


def test_invalid_adapter_preview_fails_closed_without_leaking_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class InvalidPreviewPersistence(MemoryPreviewPersistence):
        async def get_image_preview(
            self,
            *,
            generation_id: str,
            user_id: int | None,
        ) -> StoredImagePreview | None:
            return StoredImagePreview(
                generation_id,
                user_id,
                b"PRIVATE_CORRUPT_PREVIEW",
                "image/webp",
            )

    generation_id = str(uuid4())
    response = asyncio.run(
        send_request(
            "GET",
            f"/api/v1/generations/{generation_id}/image-preview",
            application=build_test_app(
                generation_persistence=InvalidPreviewPersistence()
            ),
        )
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "DATABASE_ERROR"
    assert response.headers["cache-control"] == "no-store"
    assert "Cookie" in response.headers["vary"]
    assert "PRIVATE_CORRUPT_PREVIEW" not in response.text
    assert "PRIVATE_CORRUPT_PREVIEW" not in caplog.text


def test_preview_path_rejects_non_uuid_before_persistence_lookup() -> None:
    class LookupMustNotRun(MemoryPreviewPersistence):
        async def get_image_preview(
            self,
            *,
            generation_id: str,
            user_id: int | None,
        ) -> StoredImagePreview | None:
            raise AssertionError((generation_id, user_id))

    response = asyncio.run(
        send_request(
            "GET",
            "/api/v1/generations/not-a-uuid/image-preview",
            application=build_test_app(generation_persistence=LookupMustNotRun()),
        )
    )

    assert response.status_code == 422
    assert "etc/passwd" not in response.text


def test_preview_failure_keeps_generation_successful_without_logging_filename(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fail_preview(_image: ProcessedImage) -> None:
        raise HistoryPreviewError()

    monkeypatch.setattr(
        "backend.api.v1.generations.create_history_image_preview",
        fail_preview,
    )
    persistence = MemoryPreviewPersistence()
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=build_test_app(generation_persistence=persistence),
            files={
                "image": (
                    "PRIVATE_FILENAME.png",
                    make_image_bytes(),
                    "image/png",
                )
            },
        )
    )
    history = asyncio.run(
        send_request(
            "GET",
            "/api/v1/generations",
            application=build_test_app(generation_persistence=persistence),
        )
    )

    assert response.status_code == 200
    assert history.json()["items"][0]["has_image_preview"] is False
    assert history.json()["items"][0]["image_preview_url"] is None
    assert "History image preview unavailable" in caplog.text
    assert "PRIVATE_FILENAME" not in caplog.text


def test_asset_openapi_and_cors_do_not_accept_user_id() -> None:
    application = _auth_app(MemoryPreviewPersistence(), user_id=101)
    paths = application.openapi()["paths"]
    preview_operation = paths[
        "/api/v1/generations/{generation_id}/image-preview"
    ]["get"]
    delete_operation = paths["/api/v1/generations/{generation_id}"]["delete"]
    preflight = asyncio.run(
        send_request(
            "OPTIONS",
            "/api/v1/generations/00000000-0000-4000-8000-000000000001",
            application=application,
            authenticated=False,
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "DELETE",
                "Access-Control-Request-Headers": "X-XHS-CSRF",
            },
        )
    )

    assert "user_id" not in str(preview_operation)
    assert "user_id" not in str(delete_operation)
    assert preflight.status_code == 200
    assert "DELETE" in preflight.headers["access-control-allow-methods"]
