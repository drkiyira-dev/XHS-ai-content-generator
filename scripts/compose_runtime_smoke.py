"""Run persistence-only checks inside the Compose backend container."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
import sys
from uuid import UUID, uuid4

from anyio import to_thread
from sqlalchemy import text

from backend.core.config import Settings, get_settings
from backend.schemas import RiskAssessmentSnapshot
from backend.services.persistence import (
    PendingGeneration,
    SQLAlchemyGenerationPersistence,
    StoredGeneration,
    SuccessfulGeneration,
)
from backend.services.persistence.runtime import (
    create_sqlalchemy_persistence_runtime,
)


EXPECTED_IMAGE_SUMMARY = "Compose CI 图片摘要"
EXPECTED_TITLE = "Compose持久化测试"
EXPECTED_BODY = "这是一条不调用模型的 Compose 持久化测试记录。"
EXPECTED_TAGS = ("#Compose", "#CI", "#持久化")
EXPECTED_RISK_ASSESSMENT = RiskAssessmentSnapshot(
    rule_version="compose-ci-risk-v1",
    findings=(),
)
EXPECTED_SCHEMA_PRIVILEGES = {"SELECT", "INSERT", "UPDATE"}
EXPECTED_GRANTS = {
    "GRANT USAGE ON *.* TO `xhs_app`@`%`",
    "GRANT SELECT, INSERT, UPDATE ON `xhs_ai`.* TO `xhs_app`@`%`",
}


def _verify_identity_and_privileges(
    persistence: SQLAlchemyGenerationPersistence,
) -> None:
    session_factory = persistence._session_factory
    with session_factory() as session:
        current_user = session.execute(text("SELECT CURRENT_USER()")).scalar_one()
        assert current_user == "xhs_app@%"
        grantee = "'xhs_app'@'%'"

        schema_privileges = {
            (str(schema_name), str(privilege).upper())
            for schema_name, privilege in session.execute(
                text(
                    "SELECT TABLE_SCHEMA, PRIVILEGE_TYPE "
                    "FROM information_schema.SCHEMA_PRIVILEGES "
                    "WHERE GRANTEE = :grantee"
                ),
                {"grantee": grantee},
            )
        }
        assert schema_privileges == {
            ("xhs_ai", privilege) for privilege in EXPECTED_SCHEMA_PRIVILEGES
        }

        grants = {
            " ".join(str(grant).split())
            for grant in session.execute(text("SHOW GRANTS")).scalars()
        }
        assert grants == EXPECTED_GRANTS

        global_privileges = {
            str(value).upper()
            for value in session.execute(
                text(
                    "SELECT PRIVILEGE_TYPE "
                    "FROM information_schema.USER_PRIVILEGES "
                    "WHERE GRANTEE = :grantee"
                ),
                {"grantee": grantee},
            ).scalars()
        }
        assert global_privileges <= {"USAGE"}

        applicable_roles = tuple(
            session.execute(
                text(
                    "SELECT ROLE_NAME, ROLE_HOST "
                    "FROM information_schema.APPLICABLE_ROLES"
                )
            )
        )
        assert applicable_roles == ()

        for metadata_table in (
            "TABLE_PRIVILEGES",
            "COLUMN_PRIVILEGES",
        ):
            extra_privileges = tuple(
                session.execute(
                    text(
                        "SELECT PRIVILEGE_TYPE "
                        f"FROM information_schema.{metadata_table} "
                        "WHERE GRANTEE = :grantee"
                    ),
                    {"grantee": grantee},
                ).scalars()
            )
            assert extra_privileges == ()


def _assert_upload_directory_empty(settings: Settings) -> None:
    upload_dir = settings.resolved_upload_dir
    assert upload_dir == Path("/run/xhs/uploads")
    assert upload_dir.is_dir()
    assert not any(upload_dir.iterdir())


def _assert_expected_record(
    records: tuple[StoredGeneration, ...],
    generation_id: str,
) -> None:
    assert len(records) == 1
    matching = [
        record
        for record in records
        if getattr(record, "generation_id", None) == generation_id
    ]
    assert len(matching) == 1
    record = matching[0]
    assert record.user_id is None
    assert record.image_summary == EXPECTED_IMAGE_SUMMARY
    assert record.title == EXPECTED_TITLE
    assert record.body == EXPECTED_BODY
    assert record.tags == EXPECTED_TAGS
    assert record.risk_assessment == EXPECTED_RISK_ASSESSMENT


async def _run(mode: str, generation_id: str | None) -> str | None:
    settings = get_settings()
    assert settings.database_enabled is True
    assert settings.auth_enabled is False
    runtime = create_sqlalchemy_persistence_runtime(settings)
    try:
        await runtime.startup()
        persistence = runtime.persistence
        await to_thread.run_sync(
            _verify_identity_and_privileges,
            persistence,
            abandon_on_cancel=False,
        )

        if mode == "write":
            assert generation_id is None
            generation_id = str(uuid4())
            await persistence.create_pending(
                PendingGeneration(
                    generation_id=generation_id,
                    user_id=None,
                    created_at=datetime.now(UTC),
                )
            )
            await persistence.mark_success(
                SuccessfulGeneration(
                    generation_id=generation_id,
                    user_id=None,
                    image_summary=EXPECTED_IMAGE_SUMMARY,
                    title=EXPECTED_TITLE,
                    body=EXPECTED_BODY,
                    tags=EXPECTED_TAGS,
                    risk_assessment=EXPECTED_RISK_ASSESSMENT,
                )
            )
        else:
            assert mode == "read"
            assert generation_id is not None
            assert str(UUID(generation_id)) == generation_id

        records = await persistence.list_successful(user_id=None, limit=50)
        _assert_expected_record(records, generation_id)
        _assert_upload_directory_empty(settings)
        return generation_id if mode == "write" else None
    finally:
        await runtime.aclose()


def main() -> int:
    if len(sys.argv) not in {2, 3} or sys.argv[1] not in {"write", "read"}:
        print("usage: compose_runtime_smoke.py write|read [generation_id]", file=sys.stderr)
        return 2
    mode = sys.argv[1]
    generation_id = sys.argv[2] if len(sys.argv) == 3 else None
    if (mode == "write") == (generation_id is not None):
        print("invalid smoke-test arguments", file=sys.stderr)
        return 2

    written_id = asyncio.run(_run(mode, generation_id))
    if written_id is not None:
        print(written_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
