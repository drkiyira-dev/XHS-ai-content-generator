"""Create a verified, local-only backup of pre-account generation history.

This command is intentionally separate from migrations. It opens one
read-only consistent snapshot, copies only the original generation_records
columns, and atomically publishes a permission-restricted backup directory.
It never assigns records to an account and never changes the database.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any, TextIO

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Connection, Engine, URL
from sqlalchemy.pool import NullPool

from backend.core.config import ENV_FILE, PROJECT_ROOT, Settings
from backend.db.models import GenerationRecord
from backend.services.persistence.runtime import _validated_database_url


BACKUP_ROOT = PROJECT_ROOT / ".local-backups"
CONFIRM_ENV_NAME = "XHS_HISTORY_BACKUP_CONFIRM"
CONFIRM_ENV_VALUE = "YES_BACKUP_XHS_AI_HISTORY"
MAINTENANCE_CONFIRMATION = "BACKEND_STOPPED"

BACKUP_FORMAT = "xhs-generation-records-jsonl"
BACKUP_SCHEMA_VERSION = 1
RECORDS_FILENAME = "records.jsonl"
MANIFEST_FILENAME = "manifest.json"
HISTORY_COLUMNS = (
    "id",
    "task_id",
    "status",
    "image_path",
    "image_description",
    "user_input",
    "title",
    "content",
    "tags",
    "error_code",
    "error_message",
    "created_at",
    "updated_at",
)

_FINAL_NAME_PREFIX = "generation-records-pre-user-ownership"
_SAFE_RANDOM_SUFFIX = re.compile(r"[a-f0-9]{16,64}\Z")
_SAFE_DATABASE_NAME = re.compile(r"[A-Za-z0-9_$-]{1,64}\Z")
_FILE_MODE = 0o600
_DIRECTORY_MODE = 0o700


class HistoryBackupError(RuntimeError):
    """Fixed-detail failure that cannot expose a URL or database row."""

    def __init__(self) -> None:
        super().__init__("generation history backup failed")


@dataclass(frozen=True, slots=True)
class HistorySnapshot:
    """One read-only database snapshot consumed before its transaction closes."""

    database_name: str
    row_count: int
    rows: Iterable[Mapping[str, object]] = field(repr=False)


@dataclass(frozen=True, slots=True)
class BackupResult:
    """Non-sensitive completion metadata safe to display locally."""

    directory: Path
    row_count: int
    records_sha256: str


@contextmanager
def open_history_snapshot(
    engine: Engine,
    *,
    expected_database_name: str,
) -> Iterator[HistorySnapshot]:
    """Open one read-only, consistently ordered snapshot.

    MySQL is the production path. SQLite support exists only so the backup
    contract can be tested without contacting a developer database.
    """
    connection: Connection | None = None
    result: Any = None
    setup_failed = False
    try:
        connection = engine.connect()
        _begin_read_only_snapshot(connection)
        database_name = _database_name(connection, expected_database_name)
        if database_name != expected_database_name:
            setup_failed = True
        row_count = connection.execute(
            select(func.count()).select_from(GenerationRecord)
        ).scalar_one()
        if (
            setup_failed
            or isinstance(row_count, bool)
            or not isinstance(row_count, int)
            or row_count < 0
        ):
            setup_failed = True
        if not setup_failed:
            columns = [GenerationRecord.__table__.c[name] for name in HISTORY_COLUMNS]
            result = connection.execution_options(stream_results=True).execute(
                select(*columns).order_by(GenerationRecord.id.asc())
            )
    except Exception:
        setup_failed = True

    if setup_failed or connection is None or result is None:
        _close_snapshot(connection, result)
        raise HistoryBackupError() from None

    try:
        yield HistorySnapshot(
            database_name=database_name,
            row_count=row_count,
            rows=result.mappings(),
        )
    finally:
        _close_snapshot(connection, result)


def write_history_backup(
    snapshot: HistorySnapshot,
    *,
    backup_root: Path,
    clock: Callable[[], datetime] | None = None,
    suffix_factory: Callable[[], str] | None = None,
) -> BackupResult:
    """Write, re-read, verify, and atomically publish one local backup."""
    result: BackupResult | None = None
    failed = False
    try:
        result = _write_history_backup_once(
            snapshot,
            backup_root=backup_root,
            clock=clock,
            suffix_factory=suffix_factory,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        failed = True
    if failed or result is None:
        raise HistoryBackupError() from None
    return result


def _write_history_backup_once(
    snapshot: HistorySnapshot,
    *,
    backup_root: Path,
    clock: Callable[[], datetime] | None,
    suffix_factory: Callable[[], str] | None,
) -> BackupResult:
    now = (clock or _utc_now)()
    suffix = (suffix_factory or _random_suffix)()
    timestamp = _backup_timestamp(now)
    if not isinstance(suffix, str) or _SAFE_RANDOM_SUFFIX.fullmatch(suffix) is None:
        raise HistoryBackupError() from None
    if not _valid_database_name(snapshot.database_name):
        raise HistoryBackupError() from None

    _ensure_private_backup_root(backup_root)
    final_directory = backup_root / f"{_FINAL_NAME_PREFIX}-{timestamp}-{suffix}"
    staging_directory = backup_root / f".staging-{timestamp}-{suffix}"
    if final_directory.exists() or staging_directory.exists():
        raise HistoryBackupError() from None

    staging_created = False
    try:
        os.mkdir(staging_directory, _DIRECTORY_MODE)
        staging_created = True
        os.chmod(staging_directory, _DIRECTORY_MODE, follow_symlinks=False)

        records_path = staging_directory / RECORDS_FILENAME
        records_sha256, written_count = _write_records(
            records_path,
            snapshot.rows,
        )
        if written_count != snapshot.row_count:
            raise HistoryBackupError() from None

        manifest = {
            "format": BACKUP_FORMAT,
            "schema_version": BACKUP_SCHEMA_VERSION,
            "created_at": _json_datetime(now),
            "database_name_sha256": sha256(
                snapshot.database_name.encode("utf-8")
            ).hexdigest(),
            "table": GenerationRecord.__tablename__,
            "columns": list(HISTORY_COLUMNS),
            "ordering": ["id ASC"],
            "row_count": written_count,
            "records_sha256": records_sha256,
        }
        manifest_path = staging_directory / MANIFEST_FILENAME
        _write_json_file_exclusively(manifest_path, manifest)
        _fsync_directory(staging_directory)

        _verify_staging_backup(staging_directory)
        if final_directory.exists():
            raise HistoryBackupError() from None
        os.rename(staging_directory, final_directory)
        staging_created = False
        _fsync_directory(backup_root)
    except (KeyboardInterrupt, SystemExit):
        if staging_created:
            _clean_owned_staging_directory(staging_directory)
        raise
    except BaseException:
        if staging_created:
            _clean_owned_staging_directory(staging_directory)
        raise HistoryBackupError() from None

    return BackupResult(
        directory=final_directory,
        row_count=snapshot.row_count,
        records_sha256=records_sha256,
    )


def _begin_read_only_snapshot(connection: Connection) -> None:
    dialect = connection.dialect.name
    if dialect == "mysql":
        connection.exec_driver_sql(
            "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ"
        )
        connection.exec_driver_sql(
            "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY"
        )
        return
    if dialect == "sqlite":
        connection.exec_driver_sql("PRAGMA query_only = ON")
        connection.exec_driver_sql("BEGIN")
        return
    raise HistoryBackupError() from None


def _database_name(connection: Connection, expected_database_name: str) -> str:
    if not _valid_database_name(expected_database_name):
        raise HistoryBackupError() from None
    if connection.dialect.name == "mysql":
        selected = connection.exec_driver_sql("SELECT DATABASE()").scalar_one()
        if not isinstance(selected, str) or not _valid_database_name(selected):
            raise HistoryBackupError() from None
        return selected
    return expected_database_name


def _close_snapshot(connection: Connection | None, result: Any) -> None:
    if result is not None:
        try:
            result.close()
        except Exception:
            pass
    if connection is not None:
        try:
            connection.rollback()
        except Exception:
            pass
        try:
            connection.close()
        except Exception:
            pass


def _write_records(
    path: Path,
    rows: Iterable[Mapping[str, object]],
) -> tuple[str, int]:
    digest = sha256()
    count = 0
    previous_id: int | None = None
    descriptor = _open_exclusive(path)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            for row in rows:
                record = _canonical_record(row)
                record_id = record["id"]
                if (
                    isinstance(record_id, bool)
                    or not isinstance(record_id, int)
                    or record_id < 1
                    or (previous_id is not None and record_id <= previous_id)
                ):
                    raise HistoryBackupError() from None
                previous_id = record_id
                line = (
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
                stream.write(line)
                digest.update(line)
                count += 1
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return digest.hexdigest(), count


def _canonical_record(row: Mapping[str, object]) -> dict[str, object]:
    try:
        record = {name: _json_value(row[name]) for name in HISTORY_COLUMNS}
    except Exception:
        raise HistoryBackupError() from None
    if set(record) != set(HISTORY_COLUMNS) or "user_id" in record:
        raise HistoryBackupError() from None
    return record


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return _json_datetime(value)
    # Round-trip through the encoder now so unsupported provider values fail
    # before a backup directory can be published.
    json.dumps(value, ensure_ascii=False, allow_nan=False)
    return value


def _json_datetime(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise HistoryBackupError() from None
    if value.tzinfo is None:
        normalized = value.replace(tzinfo=UTC)
    else:
        if value.utcoffset() is None:
            raise HistoryBackupError() from None
        normalized = value.astimezone(UTC)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _write_json_file_exclusively(path: Path, value: Mapping[str, object]) -> None:
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    descriptor = _open_exclusive(path)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _open_exclusive(path: Path) -> int:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    created = False
    try:
        descriptor = os.open(path, flags, _FILE_MODE)
        created = True
        os.fchmod(descriptor, _FILE_MODE)
        return descriptor
    except Exception:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except Exception:
                pass
        if created:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        raise HistoryBackupError() from None


def _verify_staging_backup(directory: Path) -> None:
    records_path = directory / RECORDS_FILENAME
    manifest_path = directory / MANIFEST_FILENAME
    _require_private_regular_file(records_path)
    _require_private_regular_file(manifest_path)
    try:
        manifest = json.loads(_read_file_safely(manifest_path).decode("utf-8"))
    except Exception:
        raise HistoryBackupError() from None
    expected_keys = {
        "format",
        "schema_version",
        "created_at",
        "database_name_sha256",
        "table",
        "columns",
        "ordering",
        "row_count",
        "records_sha256",
    }
    if (
        not isinstance(manifest, dict)
        or set(manifest) != expected_keys
        or manifest.get("format") != BACKUP_FORMAT
        or manifest.get("schema_version") != BACKUP_SCHEMA_VERSION
        or manifest.get("table") != GenerationRecord.__tablename__
        or manifest.get("columns") != list(HISTORY_COLUMNS)
        or manifest.get("ordering") != ["id ASC"]
        or not isinstance(manifest.get("row_count"), int)
        or isinstance(manifest.get("row_count"), bool)
        or manifest.get("row_count", -1) < 0
        or not isinstance(manifest.get("created_at"), str)
        or not isinstance(manifest.get("database_name_sha256"), str)
        or re.fullmatch(r"[a-f0-9]{64}", manifest["database_name_sha256"])
        is None
        or not isinstance(manifest.get("records_sha256"), str)
        or re.fullmatch(r"[a-f0-9]{64}", manifest["records_sha256"]) is None
    ):
        raise HistoryBackupError() from None

    expected_records_sha256 = manifest["records_sha256"]
    expected_row_count = manifest["row_count"]
    _verify_records_file(
        records_path,
        expected_sha256=expected_records_sha256,
        expected_row_count=expected_row_count,
    )


def _verify_records_file(
    path: Path,
    *,
    expected_sha256: str,
    expected_row_count: int,
) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except Exception:
        raise HistoryBackupError() from None
    digest = sha256()
    row_count = 0
    previous_id: int | None = None
    try:
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            for raw_line in stream:
                digest.update(raw_line)
                if not raw_line.endswith(b"\n"):
                    raise HistoryBackupError() from None
                line = raw_line[:-1]
                try:
                    record = json.loads(line)
                except Exception:
                    raise HistoryBackupError() from None
                if not isinstance(record, dict) or tuple(record) != HISTORY_COLUMNS:
                    raise HistoryBackupError() from None
                if "user_id" in record:
                    raise HistoryBackupError() from None
                canonical_line = json.dumps(
                    record,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                if line != canonical_line:
                    raise HistoryBackupError() from None
                record_id = record.get("id")
                if (
                    isinstance(record_id, bool)
                    or not isinstance(record_id, int)
                    or record_id < 1
                    or (previous_id is not None and record_id <= previous_id)
                ):
                    raise HistoryBackupError() from None
                previous_id = record_id
                row_count += 1
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if row_count != expected_row_count or digest.hexdigest() != expected_sha256:
        raise HistoryBackupError() from None


def _read_file_safely(path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except Exception:
        raise HistoryBackupError() from None
    try:
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _require_private_regular_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except Exception:
        raise HistoryBackupError() from None
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != _FILE_MODE:
        raise HistoryBackupError() from None


def _ensure_private_backup_root(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        try:
            os.mkdir(path, _DIRECTORY_MODE)
            metadata = path.lstat()
        except Exception:
            raise HistoryBackupError() from None
    except Exception:
        raise HistoryBackupError() from None
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise HistoryBackupError() from None
    try:
        os.chmod(path, _DIRECTORY_MODE, follow_symlinks=False)
    except Exception:
        raise HistoryBackupError() from None


def _clean_owned_staging_directory(path: Path) -> None:
    for filename in (RECORDS_FILENAME, MANIFEST_FILENAME):
        try:
            (path / filename).unlink(missing_ok=True)
        except Exception:
            pass
    try:
        path.rmdir()
    except Exception:
        pass


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except Exception:
        raise HistoryBackupError() from None


def _backup_timestamp(value: datetime) -> str:
    return _json_datetime(value).replace("-", "").replace(":", "").replace(".", "")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _random_suffix() -> str:
    return secrets.token_hex(16)


def _valid_database_name(value: object) -> bool:
    return isinstance(value, str) and _SAFE_DATABASE_NAME.fullmatch(value) is not None


def _require_cli_gate(
    *,
    environ: Mapping[str, str],
    stdin: TextIO,
    stdout: TextIO,
) -> None:
    if environ.get(CONFIRM_ENV_NAME) != CONFIRM_ENV_VALUE:
        raise HistoryBackupError() from None
    if not stdin.isatty() or not stdout.isatty():
        raise HistoryBackupError() from None


def _confirm_snapshot(
    snapshot: HistorySnapshot,
    *,
    stdin: TextIO,
    stdout: TextIO,
) -> None:
    stdout.write(
        "将只读备份 generation_records。请保持后端停止，"
        f"数据库：{snapshot.database_name}，记录数：{snapshot.row_count}。\n"
    )
    stdout.write("请输入数据库名确认：")
    stdout.flush()
    database_confirmation = stdin.readline(256).rstrip("\r\n")
    stdout.write("请输入记录数确认：")
    stdout.flush()
    count_confirmation = stdin.readline(64).rstrip("\r\n")
    stdout.write(f"请输入 {MAINTENANCE_CONFIRMATION} 确认后端已停止：")
    stdout.flush()
    maintenance_confirmation = stdin.readline(64).rstrip("\r\n")
    if (
        database_confirmation != snapshot.database_name
        or count_confirmation != str(snapshot.row_count)
        or maintenance_confirmation != MAINTENANCE_CONFIRMATION
    ):
        raise HistoryBackupError() from None


def _create_backup_engine(settings: Settings, database_url: URL) -> Engine:
    connect_args: dict[str, object] = {
        "charset": "utf8mb4",
        "connect_timeout": settings.database_connect_timeout_seconds,
        "read_timeout": settings.database_read_timeout_seconds,
        "write_timeout": settings.database_write_timeout_seconds,
        "local_infile": False,
    }
    if settings.database_tls_ca is not None:
        connect_args.update(
            {
                "ssl_ca": str(settings.resolved_database_tls_ca),
                "ssl_verify_cert": True,
                "ssl_verify_identity": True,
            }
        )
    try:
        return create_engine(
            database_url,
            connect_args=connect_args,
            echo=False,
            echo_pool=False,
            hide_parameters=True,
            pool_pre_ping=True,
            poolclass=NullPool,
        )
    except Exception:
        raise HistoryBackupError() from None


def main() -> int:
    engine: Engine | None = None
    try:
        _require_cli_gate(
            environ=os.environ,
            stdin=sys.stdin,
            stdout=sys.stdout,
        )
        settings = Settings(_env_file=ENV_FILE)
        database_url = _validated_database_url(settings)
        database_name = database_url.database
        if not isinstance(database_name, str) or not _valid_database_name(database_name):
            raise HistoryBackupError() from None
        engine = _create_backup_engine(settings, database_url)
        with open_history_snapshot(
            engine,
            expected_database_name=database_name,
        ) as snapshot:
            _confirm_snapshot(snapshot, stdin=sys.stdin, stdout=sys.stdout)
            result = write_history_backup(snapshot, backup_root=BACKUP_ROOT)
    except (HistoryBackupError, EOFError):
        print(
            "未创建历史备份；请确认后端已停止、数据库可用且确认信息正确。",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("未创建历史备份。", file=sys.stderr)
        return 1
    except Exception:
        print("未创建历史备份。", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                pass

    relative_directory = result.directory.relative_to(PROJECT_ROOT)
    print(
        f"历史备份完成：{relative_directory}；记录数：{result.row_count}；"
        f"SHA-256：{result.records_sha256[:12]}…"
    )
    return 0


__all__ = [
    "BACKUP_ROOT",
    "CONFIRM_ENV_NAME",
    "CONFIRM_ENV_VALUE",
    "HISTORY_COLUMNS",
    "BackupResult",
    "HistoryBackupError",
    "HistorySnapshot",
    "open_history_snapshot",
    "write_history_backup",
]


if __name__ == "__main__":
    raise SystemExit(main())
