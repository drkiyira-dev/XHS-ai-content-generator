"""Persistence contracts for private history previews and soft deletion."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db import Base, GenerationRecord, MAX_IMAGE_PREVIEW_BYTES
from backend.services.persistence import (
    GenerationPersistenceError,
    PendingGeneration,
    SQLAlchemyGenerationPersistence,
    SuccessfulGeneration,
)


TEST_USER_ID = 101
OTHER_USER_ID = 202
JPEG_PREVIEW = b"\xff\xd8\xff\xe0test-preview\xff\xd9"


def _build_adapter(
    tmp_path: Path,
) -> tuple[SQLAlchemyGenerationPersistence, sessionmaker[Session], object]:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'history-lifecycle.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return SQLAlchemyGenerationPersistence(factory), factory, engine


async def _seed_success(
    adapter: SQLAlchemyGenerationPersistence,
    *,
    generation_id: str,
    user_id: int | None,
    created_at: datetime,
    preview: bytes | None = JPEG_PREVIEW,
    media_type: str | None = "image/jpeg",
) -> None:
    await adapter.create_pending(PendingGeneration(generation_id, user_id, created_at))
    await adapter.mark_success(
        SuccessfulGeneration(
            generation_id=generation_id,
            user_id=user_id,
            image_summary="图片中是安静的湖面与山林。",
            title="湖畔慢时光",
            body="山林倒映在清澈湖面，适合记录一段安静旅程。",
            tags=("#湖景", "#山林", "#旅行"),
            image_preview=preview,
            image_preview_media_type=media_type,
        )
    )


def test_preview_is_private_owner_scoped_and_history_only_has_boolean(
    tmp_path: Path,
) -> None:
    adapter, _factory, engine = _build_adapter(tmp_path)
    generation_id = str(uuid4())
    try:
        asyncio.run(
            _seed_success(
                adapter,
                generation_id=generation_id,
                user_id=TEST_USER_ID,
                created_at=datetime.now(UTC),
            )
        )
        history = asyncio.run(
            adapter.list_successful(user_id=TEST_USER_ID, limit=20)
        )
        owned = asyncio.run(
            adapter.get_image_preview(
                generation_id=generation_id,
                user_id=TEST_USER_ID,
            )
        )
        hidden = asyncio.run(
            adapter.get_image_preview(
                generation_id=generation_id,
                user_id=OTHER_USER_ID,
            )
        )
    finally:
        engine.dispose()

    assert len(history) == 1
    assert history[0].has_image_preview is True
    assert not hasattr(history[0], "image_preview")
    assert owned is not None
    assert owned.content == JPEG_PREVIEW
    assert owned.media_type == "image/jpeg"
    assert hidden is None
    assert JPEG_PREVIEW not in repr(owned).encode()


def test_soft_delete_is_owner_scoped_idempotent_and_clears_user_content(
    tmp_path: Path,
) -> None:
    adapter, factory, engine = _build_adapter(tmp_path)
    generation_id = str(uuid4())
    created_at = datetime.now(UTC) - timedelta(minutes=1)
    deleted_at = datetime.now(UTC)
    try:
        asyncio.run(
            _seed_success(
                adapter,
                generation_id=generation_id,
                user_id=TEST_USER_ID,
                created_at=created_at,
            )
        )
        wrong_owner = asyncio.run(
            adapter.delete_successful(
                generation_id=generation_id,
                user_id=OTHER_USER_ID,
                deleted_at=deleted_at,
            )
        )
        first = asyncio.run(
            adapter.delete_successful(
                generation_id=generation_id,
                user_id=TEST_USER_ID,
                deleted_at=deleted_at,
            )
        )
        repeated = asyncio.run(
            adapter.delete_successful(
                generation_id=generation_id,
                user_id=TEST_USER_ID,
                deleted_at=deleted_at + timedelta(seconds=1),
            )
        )
        history = asyncio.run(
            adapter.list_successful(user_id=TEST_USER_ID, limit=20)
        )
        visible = asyncio.run(
            adapter.get_successful(
                generation_id=generation_id,
                user_id=TEST_USER_ID,
            )
        )
        preview = asyncio.run(
            adapter.get_image_preview(
                generation_id=generation_id,
                user_id=TEST_USER_ID,
            )
        )
        with factory() as session:
            raw = session.execute(
                select(GenerationRecord).where(
                    GenerationRecord.task_id == generation_id
                )
            ).scalar_one()
            assert raw.deleted_at == deleted_at.replace(tzinfo=None)
            assert raw.image_path is None
            assert raw.image_preview is None
            assert raw.image_preview_media_type is None
            assert raw.image_description is None
            assert raw.user_input is None
            assert raw.title is None
            assert raw.content is None
            assert raw.tags is None
    finally:
        engine.dispose()

    assert wrong_owner is None
    assert first is not None and first.deleted_now is True
    assert repeated is not None and repeated.deleted_now is False
    assert history == ()
    assert visible is None
    assert preview is None


@pytest.mark.parametrize(
    "preview,media_type",
    [
        (JPEG_PREVIEW, None),
        (None, "image/jpeg"),
        (b"not-an-image", "image/jpeg"),
        (JPEG_PREVIEW, "image/png"),
        (b"\xff\xd8\xff" + b"x" * MAX_IMAGE_PREVIEW_BYTES + b"\xff\xd9", "image/jpeg"),
    ],
)
def test_invalid_preview_pair_rolls_back_success_without_details(
    tmp_path: Path,
    preview: bytes | None,
    media_type: str | None,
) -> None:
    adapter, factory, engine = _build_adapter(tmp_path)
    generation_id = str(uuid4())
    created_at = datetime.now(UTC)
    try:
        asyncio.run(
            adapter.create_pending(
                PendingGeneration(generation_id, TEST_USER_ID, created_at)
            )
        )
        with pytest.raises(GenerationPersistenceError) as caught:
            asyncio.run(
                adapter.mark_success(
                    SuccessfulGeneration(
                        generation_id=generation_id,
                        user_id=TEST_USER_ID,
                        image_summary="图片摘要",
                        title="标题",
                        body="正文内容",
                        tags=("#一", "#二", "#三"),
                        image_preview=preview,
                        image_preview_media_type=media_type,
                    )
                )
            )
        with factory() as session:
            raw = session.execute(
                select(GenerationRecord).where(
                    GenerationRecord.task_id == generation_id
                )
            ).scalar_one()
            assert raw.status == "pending"
            assert raw.image_preview is None
    finally:
        engine.dispose()

    assert str(caught.value) == "generation persistence failed"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_legacy_null_owner_and_no_preview_remain_compatible(tmp_path: Path) -> None:
    adapter, _factory, engine = _build_adapter(tmp_path)
    generation_id = str(uuid4())
    try:
        asyncio.run(
            _seed_success(
                adapter,
                generation_id=generation_id,
                user_id=None,
                created_at=datetime.now(UTC),
                preview=None,
                media_type=None,
            )
        )
        legacy = asyncio.run(adapter.list_successful(user_id=None, limit=20))
        authenticated = asyncio.run(
            adapter.list_successful(user_id=TEST_USER_ID, limit=20)
        )
    finally:
        engine.dispose()

    assert len(legacy) == 1
    assert legacy[0].user_id is None
    assert legacy[0].has_image_preview is False
    assert authenticated == ()
