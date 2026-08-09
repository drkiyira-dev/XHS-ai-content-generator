"""SQLite contract tests for the selectively integrated member-C core."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.db import (
    Base,
    GenerationRecord,
    TASK_STATUS_FAILED,
    TASK_STATUS_PENDING,
    TASK_STATUS_SUCCESS,
    create_pending,
    mark_failed,
    mark_success,
)
from backend.schemas import BusinessException, ErrorCode
from backend.validation import validate_generation_result


@contextmanager
def database(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    """Create one isolated SQLite database without reading application config."""
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'generation-tests.sqlite3'}",
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=Session,
    )
    try:
        yield factory
    finally:
        engine.dispose()


def load_record(
    factory: sessionmaker[Session],
    generation_id: str,
) -> GenerationRecord:
    with factory() as session:
        record = session.execute(
            select(GenerationRecord).where(
                GenerationRecord.task_id == generation_id
            )
        ).scalar_one()
        session.expunge(record)
        return record


def create_one_pending(
    factory: sessionmaker[Session],
    generation_id: str,
    created_at: datetime,
) -> None:
    with factory.begin() as session:
        create_pending(
            session,
            generation_id=generation_id,
            created_at=created_at,
        )


def test_create_pending_persists_the_exact_b_generation_id(tmp_path: Path) -> None:
    generation_id = str(uuid4())
    created_at = datetime.now(UTC)

    with database(tmp_path) as factory:
        create_one_pending(factory, generation_id, created_at)
        record = load_record(factory, generation_id)

    assert record.task_id == generation_id
    assert record.generation_id == generation_id
    assert record.status == TASK_STATUS_PENDING
    assert record.created_at == created_at.replace(tzinfo=None)
    assert record.title is None
    assert record.error_code is None


def test_success_runs_c_validation_and_cannot_be_reversed(tmp_path: Path) -> None:
    generation_id = str(uuid4())
    created_at = datetime.now(UTC)

    with database(tmp_path) as factory:
        create_one_pending(factory, generation_id, created_at)
        with factory.begin() as session:
            success = mark_success(
                session,
                generation_id=generation_id,
                image_summary="海边可以看到一只白色帆布包。",
                title="海边帆布包",
                body="白色包身放在浅色沙滩上。",
                tags=("帆布包", "#海边", "帆布包", "日常穿搭"),
                completed_at=created_at + timedelta(seconds=2),
            )

        assert success.status == TASK_STATUS_SUCCESS
        assert success.tags == ["#帆布包", "#海边", "#日常穿搭"]
        assert success.image_description == "海边可以看到一只白色帆布包。"
        assert success.content == "白色包身放在浅色沙滩上。"

        with pytest.raises(BusinessException) as caught:
            with factory.begin() as session:
                mark_failed(
                    session,
                    generation_id=generation_id,
                    error_code="MODEL_FAILED",
                    failed_at=created_at + timedelta(seconds=3),
                )

        record = load_record(factory, generation_id)

    assert caught.value.code == ErrorCode.INVALID_STATE_TRANSITION
    assert record.status == TASK_STATUS_SUCCESS
    assert record.error_code is None


def test_same_session_transition_refreshes_the_loaded_pending_record(
    tmp_path: Path,
) -> None:
    generation_id = str(uuid4())
    created_at = datetime.now(UTC)

    with database(tmp_path) as factory:
        with factory.begin() as session:
            pending = create_pending(
                session,
                generation_id=generation_id,
                created_at=created_at,
            )
            success = mark_success(
                session,
                generation_id=generation_id,
                image_summary="图片中是一只白色包。",
                title="白色包袋",
                body="白色包身配有黑色提手。",
                tags=("#包袋", "#通勤", "#日常"),
                completed_at=created_at + timedelta(seconds=1),
            )

            assert success is pending
            assert success.status == TASK_STATUS_SUCCESS
            assert pending.status == TASK_STATUS_SUCCESS
            assert success.title == "白色包袋"


def test_failure_stores_only_stable_code_and_cannot_be_reversed(
    tmp_path: Path,
) -> None:
    generation_id = str(uuid4())
    created_at = datetime.now(UTC)

    with database(tmp_path) as factory:
        create_one_pending(factory, generation_id, created_at)
        with factory.begin() as session:
            failed = mark_failed(
                session,
                generation_id=generation_id,
                error_code="MODEL_TIMEOUT",
                failed_at=created_at + timedelta(seconds=2),
            )

        assert failed.status == TASK_STATUS_FAILED
        assert failed.error_code == "MODEL_TIMEOUT"
        assert failed.error_message is None

        with pytest.raises(BusinessException) as caught:
            with factory.begin() as session:
                mark_success(
                    session,
                    generation_id=generation_id,
                    image_summary="图片描述",
                    title="标题",
                    body="正文内容",
                    tags=("#一", "#二", "#三"),
                    completed_at=created_at + timedelta(seconds=3),
                )

        record = load_record(factory, generation_id)

    assert caught.value.code == ErrorCode.INVALID_STATE_TRANSITION
    assert record.status == TASK_STATUS_FAILED
    assert record.title is None


def test_invalid_copy_leaves_the_database_record_pending(tmp_path: Path) -> None:
    generation_id = str(uuid4())
    created_at = datetime.now(UTC)

    with database(tmp_path) as factory:
        create_one_pending(factory, generation_id, created_at)

        with pytest.raises(BusinessException) as caught:
            with factory.begin() as session:
                mark_success(
                    session,
                    generation_id=generation_id,
                    image_summary="图片描述",
                    title=(
                        "这个标题已经明显超过二十个汉字"
                        "所以不能写入数据库"
                    ),
                    body="正文内容",
                    tags=("#一", "#二", "#三"),
                    completed_at=created_at + timedelta(seconds=2),
                )

        record = load_record(factory, generation_id)

    assert caught.value.code == ErrorCode.VALIDATION_ERROR
    assert record.status == TASK_STATUS_PENDING
    assert record.title is None


def test_validate_generation_result_supports_b_field_names() -> None:
    normalized = validate_generation_result(
        {
            "image_summary": "  图片中是一只白色包。 ",
            "title": " 白色包袋 ",
            "body": " 白色包身配有黑色提手。 ",
            "tags": ["包袋", "#通勤", "包袋", "日常"],
        }
    )

    assert normalized == {
        "image_description": "图片中是一只白色包。",
        "image_summary": "图片中是一只白色包。",
        "title": "白色包袋",
        "content": "白色包身配有黑色提手。",
        "body": "白色包身配有黑色提手。",
        "tags": ["#包袋", "#通勤", "#日常"],
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("image_summary", {"private": "value"}),
        ("title", 123),
        ("body", ["private", "value"]),
        ("tags", 123),
        ("tags", ["#一", 2, "#三"]),
    ],
)
def test_c_validation_rejects_wrong_types_without_coercion(
    field: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "image_summary": "图片描述",
        "title": "标题",
        "body": "正文内容",
        "tags": ["#一", "#二", "#三"],
    }
    payload[field] = value

    with pytest.raises(BusinessException) as caught:
        validate_generation_result(payload)

    assert caught.value.code == ErrorCode.VALIDATION_ERROR


@pytest.mark.parametrize(
    "tags",
    [
        ["#一", "#二"],
        ["#一", "#二", "#三", "#四", "#五", "#六"],
        ["#重复", "重复", "#另一个"],
    ],
)
def test_c_validation_rejects_tag_counts_outside_three_to_five(
    tags: list[str],
) -> None:
    with pytest.raises(BusinessException) as caught:
        validate_generation_result(
            {
                "image_summary": "图片描述",
                "title": "标题",
                "body": "正文内容",
                "tags": tags,
            }
        )

    assert caught.value.code == ErrorCode.VALIDATION_ERROR


def test_invalid_failure_code_does_not_change_pending_record(tmp_path: Path) -> None:
    generation_id = str(uuid4())
    created_at = datetime.now(UTC)

    with database(tmp_path) as factory:
        create_one_pending(factory, generation_id, created_at)
        with pytest.raises(BusinessException) as caught:
            with factory.begin() as session:
                mark_failed(
                    session,
                    generation_id=generation_id,
                    error_code="private database message",
                    failed_at=created_at + timedelta(seconds=1),
                )
        record = load_record(factory, generation_id)

    assert caught.value.code == ErrorCode.VALIDATION_ERROR
    assert record.status == TASK_STATUS_PENDING
    assert record.error_code is None


def test_generation_id_with_whitespace_is_rejected_not_rewritten(
    tmp_path: Path,
) -> None:
    generation_id = str(uuid4())

    with database(tmp_path) as factory:
        with pytest.raises(BusinessException) as caught:
            with factory.begin() as session:
                create_pending(
                    session,
                    generation_id=f" {generation_id}",
                    created_at=datetime.now(UTC),
                )
        with factory() as session:
            record_count = session.scalar(
                select(func.count()).select_from(GenerationRecord)
            )

    assert caught.value.code == ErrorCode.VALIDATION_ERROR
    assert record_count == 0


def test_duplicate_generation_id_rolls_back_without_duplicate_row(
    tmp_path: Path,
) -> None:
    generation_id = str(uuid4())
    created_at = datetime.now(UTC)

    with database(tmp_path) as factory:
        create_one_pending(factory, generation_id, created_at)
        with pytest.raises(IntegrityError):
            with factory.begin() as session:
                create_pending(
                    session,
                    generation_id=generation_id,
                    created_at=created_at,
                )
        with factory() as session:
            record_count = session.scalar(
                select(func.count()).select_from(GenerationRecord)
            )

    assert record_count == 1
