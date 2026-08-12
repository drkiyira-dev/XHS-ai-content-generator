"""Contracts for persistent bounded previews and soft-deleted history."""

from pathlib import Path
import re

import pytest
from sqlalchemy.dialects import mysql
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from backend.db import Base, GenerationRecord, MAX_IMAGE_PREVIEW_BYTES


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    PROJECT_ROOT / "migrations" / "005_generation_previews_and_deletion.sql"
)


def _executable_sql() -> str:
    return "\n".join(
        line
        for line in MIGRATION_PATH.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    ).strip()


def test_migration_is_one_nullable_alter_without_data_rewrite_or_delete() -> None:
    sql = _executable_sql()
    normalized = re.sub(r"\s+", " ", sql).upper()

    assert normalized.startswith("ALTER TABLE GENERATION_RECORDS ")
    assert normalized.count(";") == 1
    assert "IMAGE_PREVIEW MEDIUMBLOB NULL" in normalized
    assert "IMAGE_PREVIEW_MEDIA_TYPE VARCHAR(32)" in normalized
    assert "IMAGE_PREVIEW_MEDIA_TYPE IS NOT NULL" in normalized
    assert "DELETED_AT DATETIME(6) NULL" in normalized
    assert "OCTET_LENGTH(IMAGE_PREVIEW) BETWEEN 1 AND 262144" in normalized
    assert "'IMAGE/WEBP', 'IMAGE/JPEG'" in normalized
    assert not re.search(
        r"\b(?:UPDATE|DELETE|DROP|TRUNCATE|RENAME|GRANT|USE)\b"
        r"|\bCREATE\s+DATABASE\b",
        normalized,
    )
    assert "DEFAULT" not in normalized


def test_orm_preview_is_deferred_bounded_and_all_new_columns_are_nullable() -> None:
    table = GenerationRecord.__table__

    assert table.c.image_preview.nullable is True
    assert table.c.image_preview_media_type.nullable is True
    assert table.c.deleted_at.nullable is True
    assert GenerationRecord.image_preview.property.deferred is True
    assert MAX_IMAGE_PREVIEW_BYTES == 262144

    mysql_ddl = str(CreateTable(table).compile(dialect=mysql.dialect()))
    assert "image_preview MEDIUMBLOB" in mysql_ddl
    assert "image_preview_media_type VARCHAR(32)" in mysql_ddl
    assert "image_preview MEDIUMBLOB NOT NULL" not in mysql_ddl
    assert "deleted_at DATETIME(6)" in mysql_ddl
    assert "ck_generation_records_image_preview" in mysql_ddl
    assert "ck_generation_records_deleted_after_created" in mysql_ddl


def test_model_dictionary_exposes_only_preview_availability_not_blob() -> None:
    record = GenerationRecord(
        task_id="00000000-0000-4000-8000-000000000001",
        status="success",
        image_preview=b"private-preview-bytes",
        image_preview_media_type="image/webp",
    )

    payload = record.to_dict()

    assert payload["has_image_preview"] is True
    assert "image_preview" not in payload
    assert "image_preview_media_type" not in payload
    assert b"private-preview-bytes" not in payload.values()


def test_database_rejects_preview_bytes_without_media_type() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(
            GenerationRecord(
                task_id="00000000-0000-4000-8000-000000000002",
                status="success",
                image_preview=b"RIFFxxxxWEBP",
                image_preview_media_type=None,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
