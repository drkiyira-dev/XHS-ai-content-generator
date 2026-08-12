"""Schema contracts for the nullable generation-ownership migration phase."""

from datetime import datetime
from pathlib import Path
import re

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable
from sqlalchemy.orm import Session

from backend.db import Base, GenerationRecord, User


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = PROJECT_ROOT / "migrations" / "004_generation_ownership.sql"


def test_ownership_migration_is_one_nullable_schema_change_without_backfill() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    sql = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("--")
    ).strip()
    normalized = re.sub(r"\s+", " ", sql).strip().upper()

    assert normalized == (
        "ALTER TABLE GENERATION_RECORDS "
        "ADD COLUMN USER_ID BIGINT UNSIGNED NULL AFTER ID, "
        "ADD INDEX IX_GENERATION_RECORDS_USER_STATUS_CREATED "
        "( USER_ID, STATUS, CREATED_AT ), "
        "ADD CONSTRAINT FK_GENERATION_RECORDS_USER_ID FOREIGN KEY (USER_ID) "
        "REFERENCES USERS (ID) ON DELETE RESTRICT ON UPDATE RESTRICT;"
    )
    assert not re.search(r"\bUPDATE\s+GENERATION_RECORDS\b", normalized)
    assert not re.search(r"\bUSER_ID\b[^,;]*\bNOT\s+NULL\b", normalized)
    assert not re.search(r"\bUSER_ID\b[^,;]*\bDEFAULT\b", normalized)


def test_generation_owner_mapping_is_nullable_indexed_and_restrictive() -> None:
    table = GenerationRecord.__table__
    owner = table.c.user_id

    assert owner.nullable is True
    assert str(owner.type.compile(dialect=mysql.dialect())) == "BIGINT UNSIGNED"
    foreign_keys = list(owner.foreign_keys)
    assert len(foreign_keys) == 1
    assert foreign_keys[0].target_fullname == "users.id"
    assert foreign_keys[0].constraint.name == "fk_generation_records_user_id"
    assert foreign_keys[0].ondelete == "RESTRICT"
    assert foreign_keys[0].onupdate == "RESTRICT"

    matching_indexes = [
        index
        for index in table.indexes
        if index.name == "ix_generation_records_user_status_created"
    ]
    assert len(matching_indexes) == 1
    assert tuple(column.name for column in matching_indexes[0].columns) == (
        "user_id",
        "status",
        "created_at",
    )

    mysql_ddl = str(CreateTable(table).compile(dialect=mysql.dialect()))
    assert "user_id BIGINT UNSIGNED" in mysql_ddl
    assert "user_id BIGINT UNSIGNED NOT NULL" not in mysql_ddl


def test_sqlite_preserves_legacy_null_owner_and_only_stores_explicit_owner(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'ownership.sqlite3'}")
    try:
        Base.metadata.create_all(engine)
        schema = inspect(engine)
        reflected_owner = next(
            column
            for column in schema.get_columns("generation_records")
            if column["name"] == "user_id"
        )
        assert reflected_owner["nullable"] is True

        now = datetime(2026, 8, 12, 0, 0, 0)
        with Session(engine) as session:
            user = User(
                email="owner@example.com",
                password_hash="test-only-password-hash",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(user)
            session.flush()
            session.add_all(
                [
                    GenerationRecord(
                        task_id="00000000-0000-4000-8000-000000000001",
                        status="pending",
                        created_at=now,
                        updated_at=now,
                    ),
                    GenerationRecord(
                        user_id=user.id,
                        task_id="00000000-0000-4000-8000-000000000002",
                        status="pending",
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
            session.commit()

        with Session(engine) as session:
            records = session.execute(
                select(GenerationRecord).order_by(GenerationRecord.id)
            ).scalars().all()
            assert [record.user_id for record in records] == [None, 1]
    finally:
        engine.dispose()
