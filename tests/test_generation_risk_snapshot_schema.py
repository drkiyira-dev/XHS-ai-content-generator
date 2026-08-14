"""Contracts for the nullable generation-time risk snapshot migration."""

from pathlib import Path
import re

from sqlalchemy import JSON
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from backend.db import GenerationRecord


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = PROJECT_ROOT / "migrations" / "006_generation_risk_snapshot.sql"


def _executable_sql() -> str:
    return "\n".join(
        line
        for line in MIGRATION_PATH.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    ).strip()


def test_migration_is_one_nullable_json_alter_without_backfill() -> None:
    sql = _executable_sql()
    normalized = re.sub(r"\s+", " ", sql).upper()

    assert normalized == (
        "ALTER TABLE GENERATION_RECORDS "
        "ADD COLUMN RISK_ASSESSMENT JSON NULL AFTER TAGS;"
    )
    assert normalized.count(";") == 1
    assert not re.search(
        r"\b(?:UPDATE|DELETE|DROP|TRUNCATE|RENAME|GRANT|USE)\b"
        r"|\bCREATE\s+DATABASE\b",
        normalized,
    )
    assert "DEFAULT" not in normalized


def test_orm_snapshot_column_is_nullable_native_json() -> None:
    column = GenerationRecord.__table__.c.risk_assessment

    assert column.nullable is True
    assert isinstance(column.type, JSON)
    assert column.type.none_as_null is True
    mysql_dialect = mysql.dialect()
    mysql_type = column.type.dialect_impl(mysql_dialect)
    mysql_processor = mysql_type.bind_processor(mysql_dialect)
    assert mysql_processor is not None
    assert mysql_processor(None) is None
    mysql_ddl = str(
        CreateTable(GenerationRecord.__table__).compile(dialect=mysql_dialect)
    )
    assert "risk_assessment JSON" in mysql_ddl
    assert "risk_assessment JSON NOT NULL" not in mysql_ddl


def test_legacy_record_dictionary_preserves_explicit_missing_snapshot() -> None:
    record = GenerationRecord(
        task_id="00000000-0000-4000-8000-000000000001",
        status="success",
        risk_assessment=None,
    )

    assert record.to_dict()["risk_assessment"] is None
