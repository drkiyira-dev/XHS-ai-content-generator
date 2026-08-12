"""Safety contracts for the offline pre-ownership history backup."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

import scripts.backup_generation_history as backup_module
from backend.db import GenerationRecord
from scripts.backup_generation_history import (
    BACKUP_FORMAT,
    BACKUP_SCHEMA_VERSION,
    CONFIRM_ENV_NAME,
    CONFIRM_ENV_VALUE,
    HISTORY_COLUMNS,
    MANIFEST_FILENAME,
    MAINTENANCE_CONFIRMATION,
    RECORDS_FILENAME,
    HistoryBackupError,
    HistorySnapshot,
    open_history_snapshot,
    write_history_backup,
)


FIXED_NOW = datetime(2026, 8, 12, 8, 30, 45, 123456, tzinfo=UTC)
FIXED_SUFFIX = "0123456789abcdef"


class TTYStringIO(StringIO):
    def isatty(self) -> bool:
        return True


def _record(record_id: int, *, status: str = "success") -> dict[str, object]:
    return {
        "id": record_id,
        "task_id": f"00000000-0000-4000-8000-{record_id:012d}",
        "status": status,
        "image_path": None,
        "image_description": "湖面与山林，中文摘要。" if status == "success" else None,
        "user_input": None,
        "title": "安静的湖边时刻" if status == "success" else None,
        "content": "把风景留在今天，也把心情慢下来。" if status == "success" else None,
        "tags": ["#湖景", "#徒步旅行"] if status == "success" else None,
        "error_code": None if status == "success" else "MODEL_FAILED",
        "error_message": None,
        "created_at": datetime(2026, 8, 12, 1, record_id, 0),
        "updated_at": datetime(2026, 8, 12, 1, record_id, 1),
    }


def _sqlite_history_engine(tmp_path: Path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'history-backup.sqlite3'}"
    )
    with engine.begin() as connection:
        # Reproduce the pre-ownership schema exactly. The current ORM may
        # already know about a future user_id column, but this backup must run
        # safely before that migration exists in the real database.
        connection.exec_driver_sql(
            """
            CREATE TABLE generation_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id VARCHAR(64) NOT NULL UNIQUE,
                status VARCHAR(16) NOT NULL,
                image_path VARCHAR(512),
                image_description TEXT,
                user_input TEXT,
                title VARCHAR(100),
                content TEXT,
                tags JSON,
                error_code VARCHAR(64),
                error_message TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        connection.execute(
            GenerationRecord.__table__.insert(),
            [_record(2), _record(1, status="failed")],
        )
    return engine


def _write_from_rows(
    tmp_path: Path,
    rows: list[dict[str, object]],
    *,
    row_count: int | None = None,
):
    return write_history_backup(
        HistorySnapshot(
            database_name="xhs_ai_test",
            row_count=len(rows) if row_count is None else row_count,
            rows=rows,
        ),
        backup_root=tmp_path / ".local-backups",
        clock=lambda: FIXED_NOW,
        suffix_factory=lambda: FIXED_SUFFIX,
    )


def test_sqlite_snapshot_is_read_only_consistent_and_selects_only_legacy_fields(
    tmp_path: Path,
) -> None:
    engine = _sqlite_history_engine(tmp_path)
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture_sql(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(" ".join(statement.split()))

    try:
        with open_history_snapshot(
            engine,
            expected_database_name="history_test",
        ) as snapshot:
            assert snapshot.database_name == "history_test"
            assert snapshot.row_count == 2
            rows = [dict(row) for row in snapshot.rows]
    finally:
        engine.dispose()

    assert [row["id"] for row in rows] == [1, 2]
    assert all(tuple(row) == HISTORY_COLUMNS for row in rows)
    assert all("user_id" not in row for row in rows)
    normalized_sql = "\n".join(statements).upper()
    assert "PRAGMA QUERY_ONLY = ON" in normalized_sql
    assert "BEGIN" in normalized_sql
    assert "SELECT COUNT(" in normalized_sql
    assert "ORDER BY GENERATION_RECORDS.ID ASC" in normalized_sql
    assert not any(
        keyword in normalized_sql
        for keyword in ("INSERT ", "UPDATE ", "DELETE ", "ALTER ", "DROP ")
    )


def test_sqlite_query_only_mode_rejects_an_accidental_write(tmp_path: Path) -> None:
    engine = _sqlite_history_engine(tmp_path)
    try:
        with engine.connect() as connection:
            backup_module._begin_read_only_snapshot(connection)
            with pytest.raises(OperationalError):
                connection.exec_driver_sql(
                    "UPDATE generation_records SET status='failed' WHERE id=1"
                )
            connection.rollback()
    finally:
        engine.dispose()


def test_mysql_snapshot_starts_repeatable_read_only_before_any_select() -> None:
    class ConnectionDouble:
        dialect = SimpleNamespace(name="mysql")

        def __init__(self) -> None:
            self.statements: list[str] = []

        def exec_driver_sql(self, statement: str) -> None:
            self.statements.append(statement)

    connection = ConnectionDouble()

    backup_module._begin_read_only_snapshot(connection)  # type: ignore[arg-type]

    assert connection.statements == [
        "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ",
        "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY",
    ]


def test_backup_engine_hides_parameters_disables_local_infile_and_pooling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    expected_engine = object()

    def capture_create_engine(url: object, **options: object) -> object:
        captured["url"] = url
        captured.update(options)
        return expected_engine

    settings = SimpleNamespace(
        database_connect_timeout_seconds=5,
        database_read_timeout_seconds=10,
        database_write_timeout_seconds=10,
        database_tls_ca=None,
    )
    url = make_url(
        "mysql+pymysql://xhs_app:PRIVATE_PASSWORD@127.0.0.1/xhs_ai_test"
    )
    monkeypatch.setattr(backup_module, "create_engine", capture_create_engine)

    result = backup_module._create_backup_engine(settings, url)

    assert result is expected_engine
    assert captured["url"] is url
    assert captured["echo"] is False
    assert captured["echo_pool"] is False
    assert captured["hide_parameters"] is True
    assert captured["pool_pre_ping"] is True
    assert captured["poolclass"] is backup_module.NullPool
    assert captured["connect_args"] == {
        "charset": "utf8mb4",
        "connect_timeout": 5,
        "read_timeout": 10,
        "write_timeout": 10,
        "local_infile": False,
    }


def test_verified_backup_has_exact_fields_hash_order_and_private_permissions(
    tmp_path: Path,
) -> None:
    engine = _sqlite_history_engine(tmp_path)
    backup_root = tmp_path / ".local-backups"
    try:
        with open_history_snapshot(
            engine,
            expected_database_name="history_test",
        ) as snapshot:
            result = write_history_backup(
                snapshot,
                backup_root=backup_root,
                clock=lambda: FIXED_NOW,
                suffix_factory=lambda: FIXED_SUFFIX,
            )
    finally:
        engine.dispose()

    assert result.directory.parent == backup_root
    assert result.directory.name.startswith(
        "generation-records-pre-user-ownership-20260812T083045123456Z-"
    )
    assert not any(path.name.startswith(".staging-") for path in backup_root.iterdir())
    assert (backup_root.stat().st_mode & 0o777) == 0o700
    assert (result.directory.stat().st_mode & 0o777) == 0o700
    records_path = result.directory / RECORDS_FILENAME
    manifest_path = result.directory / MANIFEST_FILENAME
    assert (records_path.stat().st_mode & 0o777) == 0o600
    assert (manifest_path.stat().st_mode & 0o777) == 0o600

    records_bytes = records_path.read_bytes()
    records = [json.loads(line) for line in records_bytes.splitlines()]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert [record["id"] for record in records] == [1, 2]
    assert all(tuple(record) == HISTORY_COLUMNS for record in records)
    assert all("user_id" not in record for record in records)
    assert manifest == {
        "format": BACKUP_FORMAT,
        "schema_version": BACKUP_SCHEMA_VERSION,
        "created_at": "2026-08-12T08:30:45.123456Z",
        "database_name_sha256": sha256(b"history_test").hexdigest(),
        "table": "generation_records",
        "columns": list(HISTORY_COLUMNS),
        "ordering": ["id ASC"],
        "row_count": 2,
        "records_sha256": sha256(records_bytes).hexdigest(),
    }
    assert result.row_count == 2
    assert result.records_sha256 == manifest["records_sha256"]
    assert "history_test" not in manifest_path.read_text(encoding="utf-8")
    assert "DATABASE_URL" not in manifest_path.read_text(encoding="utf-8")


def test_empty_snapshot_still_creates_a_verified_backup(tmp_path: Path) -> None:
    result = _write_from_rows(tmp_path, [])
    records_path = result.directory / RECORDS_FILENAME
    manifest = json.loads(
        (result.directory / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )

    assert records_path.read_bytes() == b""
    assert result.row_count == 0
    assert manifest["row_count"] == 0
    assert manifest["records_sha256"] == sha256(b"").hexdigest()


@pytest.mark.parametrize(
    ("rows", "row_count"),
    [
        ([_record(1)], 2),
        ([_record(2), _record(1)], 2),
        ([{**_record(1), "tags": {"not-json-serializable"}}], 1),
    ],
)
def test_failed_write_removes_only_its_staging_directory(
    tmp_path: Path,
    rows: list[dict[str, object]],
    row_count: int,
) -> None:
    backup_root = tmp_path / ".local-backups"
    backup_root.mkdir(mode=0o700)
    preserved = backup_root / "existing-backup"
    preserved.mkdir()
    marker = preserved / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(HistoryBackupError):
        _write_from_rows(tmp_path, rows, row_count=row_count)

    assert marker.read_text(encoding="utf-8") == "keep"
    assert list(backup_root.iterdir()) == [preserved]


def test_public_failure_and_snapshot_repr_never_expose_a_private_row(
    tmp_path: Path,
) -> None:
    private_detail = "PRIVATE HISTORY CONTENT SENTINEL"
    unsafe_row = {
        **_record(1),
        "content": private_detail,
        "tags": {"not-json-serializable"},
    }
    snapshot = HistorySnapshot("xhs_ai_test", 1, [unsafe_row])

    with pytest.raises(HistoryBackupError) as caught:
        write_history_backup(
            snapshot,
            backup_root=tmp_path / ".local-backups",
            clock=lambda: FIXED_NOW,
            suffix_factory=lambda: FIXED_SUFFIX,
        )

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert private_detail not in str(caught.value)
    assert private_detail not in repr(caught.value)
    assert private_detail not in repr(snapshot)


def test_existing_final_directory_is_never_overwritten(tmp_path: Path) -> None:
    backup_root = tmp_path / ".local-backups"
    backup_root.mkdir(mode=0o700)
    final_directory = backup_root / (
        "generation-records-pre-user-ownership-"
        "20260812T083045123456Z-0123456789abcdef"
    )
    final_directory.mkdir()
    marker = final_directory / "keep.txt"
    marker.write_text("original", encoding="utf-8")

    with pytest.raises(HistoryBackupError):
        _write_from_rows(tmp_path, [_record(1)])

    assert marker.read_text(encoding="utf-8") == "original"
    assert not any(path.name.startswith(".staging-") for path in backup_root.iterdir())


def test_symlink_backup_root_is_rejected_without_touching_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    (tmp_path / ".local-backups").symlink_to(target, target_is_directory=True)

    with pytest.raises(HistoryBackupError):
        _write_from_rows(tmp_path, [_record(1)])

    assert marker.read_text(encoding="utf-8") == "keep"
    assert list(target.iterdir()) == [marker]


def test_exclusive_file_open_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "existing"
    path.write_text("original", encoding="utf-8")

    with pytest.raises(HistoryBackupError):
        backup_module._open_exclusive(path)

    assert path.read_text(encoding="utf-8") == "original"


def test_published_backup_detects_later_corruption(tmp_path: Path) -> None:
    result = _write_from_rows(tmp_path, [_record(1)])
    records_path = result.directory / RECORDS_FILENAME
    records_path.write_bytes(records_path.read_bytes() + b"{}\n")
    os.chmod(records_path, 0o600)

    with pytest.raises(HistoryBackupError):
        backup_module._verify_staging_backup(result.directory)


def test_write_uses_exclusive_nofollow_files_and_fsyncs_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_open = backup_module.os.open
    real_fsync = backup_module.os.fsync
    open_flags: list[int] = []
    fsync_calls = 0

    def recording_open(path: object, flags: int, mode: int = 0o777) -> int:
        open_flags.append(flags)
        return real_open(path, flags, mode)

    def recording_fsync(descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        real_fsync(descriptor)

    monkeypatch.setattr(backup_module.os, "open", recording_open)
    monkeypatch.setattr(backup_module.os, "fsync", recording_fsync)

    _write_from_rows(tmp_path, [_record(1)])

    write_flags = [flags for flags in open_flags if flags & os.O_WRONLY]
    assert len(write_flags) == 2
    assert all(flags & os.O_EXCL for flags in write_flags)
    if hasattr(os, "O_NOFOLLOW"):
        assert all(flags & os.O_NOFOLLOW for flags in open_flags)
    assert fsync_calls >= 4


def test_cli_gate_requires_exact_environment_and_real_tty() -> None:
    with pytest.raises(HistoryBackupError):
        backup_module._require_cli_gate(
            environ={},
            stdin=TTYStringIO(),
            stdout=TTYStringIO(),
        )
    with pytest.raises(HistoryBackupError):
        backup_module._require_cli_gate(
            environ={CONFIRM_ENV_NAME: CONFIRM_ENV_VALUE},
            stdin=StringIO(),
            stdout=TTYStringIO(),
        )

    backup_module._require_cli_gate(
        environ={CONFIRM_ENV_NAME: CONFIRM_ENV_VALUE},
        stdin=TTYStringIO(),
        stdout=TTYStringIO(),
    )


def test_interactive_confirmation_requires_database_count_and_stopped_backend() -> None:
    snapshot = HistorySnapshot("xhs_ai_test", 37, ())
    accepted_input = TTYStringIO(
        f"xhs_ai_test\n37\n{MAINTENANCE_CONFIRMATION}\n"
    )
    output = TTYStringIO()

    backup_module._confirm_snapshot(
        snapshot,
        stdin=accepted_input,
        stdout=output,
    )

    assert "xhs_ai_test" in output.getvalue()
    assert "37" in output.getvalue()
    assert "DATABASE_URL" not in output.getvalue()
    with pytest.raises(HistoryBackupError):
        backup_module._confirm_snapshot(
            snapshot,
            stdin=TTYStringIO("xhs_ai_test\n36\nBACKEND_STOPPED\n"),
            stdout=TTYStringIO(),
        )


def test_main_refuses_before_loading_settings_or_connecting_without_gate(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(CONFIRM_ENV_NAME, raising=False)
    monkeypatch.setattr(
        backup_module,
        "Settings",
        lambda **_kwargs: pytest.fail("settings must not be loaded"),
    )

    status = backup_module.main()

    captured = capsys.readouterr()
    assert status == 1
    assert "未创建历史备份" in captured.err
    assert "DATABASE_URL" not in captured.err


def test_cli_failure_never_prints_private_exception_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_detail = "mysql+pymysql://private:password@127.0.0.1/private"
    stdin = TTYStringIO()
    stdout = TTYStringIO()
    stderr = TTYStringIO()
    monkeypatch.setenv(CONFIRM_ENV_NAME, CONFIRM_ENV_VALUE)
    monkeypatch.setattr(backup_module.sys, "stdin", stdin)
    monkeypatch.setattr(backup_module.sys, "stdout", stdout)
    monkeypatch.setattr(backup_module.sys, "stderr", stderr)

    def fail_settings(**_kwargs: object) -> object:
        raise RuntimeError(private_detail)

    monkeypatch.setattr(backup_module, "Settings", fail_settings)

    assert backup_module.main() == 1
    assert private_detail not in stdout.getvalue() + stderr.getvalue()
    assert "password" not in stdout.getvalue().casefold() + stderr.getvalue().casefold()


def test_backup_scope_is_frozen_to_legacy_columns_and_gitignored() -> None:
    assert len(HISTORY_COLUMNS) == 13
    assert "user_id" not in HISTORY_COLUMNS
    assert not any(name.startswith(("auth_", "password", "token")) for name in HISTORY_COLUMNS)
    assert backup_module.BACKUP_ROOT == backup_module.PROJECT_ROOT / ".local-backups"
    gitignore = (backup_module.PROJECT_ROOT / ".gitignore").read_text(
        encoding="utf-8"
    )
    assert ".local-backups/" in gitignore.splitlines()
