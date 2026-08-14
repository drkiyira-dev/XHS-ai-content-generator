"""Tests for explicit MySQL runtime configuration and app ownership."""

import asyncio
from collections.abc import Callable
from pathlib import Path
import re
from typing import Any, cast

from anyio import CapacityLimiter
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

import backend.app as app_module
import backend.services.persistence.runtime as runtime_module
from backend.db import AuthSession, Base, GenerationRecord, User
from backend.services.auth import SQLAlchemyAuthStore
from backend.services.persistence import (
    GenerationPersistence,
    NoOpGenerationPersistence,
    SQLAlchemyGenerationPersistence,
)
from backend.services.persistence.runtime import (
    DatabaseShutdownError,
    DatabaseStartupError,
    SQLAlchemyPersistenceRuntime,
    create_sqlalchemy_persistence_runtime,
)
from tests.support import build_test_app


DATABASE_URL = (
    "mysql+pymysql://xhs_app:test-only-password@"
    "127.0.0.1:3306/xhs_ai_test"
)


class RecordingRuntime:
    """Runtime double that records lifespan ownership without a database."""

    def __init__(
        self,
        *,
        startup_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.persistence = NoOpGenerationPersistence()
        self.startup_error = startup_error
        self.close_error = close_error
        self.startup_calls = 0
        self.close_calls = 0

    async def startup(self) -> None:
        self.startup_calls += 1
        if self.startup_error is not None:
            raise self.startup_error

    async def aclose(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


def run_lifespan(application: Any, assertion: Callable[[], None]) -> None:
    async def exercise() -> None:
        async with application.router.lifespan_context(application):
            assertion()

    asyncio.run(exercise())


def test_database_disabled_never_builds_a_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_factory(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("database runtime must stay disabled")

    monkeypatch.setattr(
        app_module,
        "create_sqlalchemy_persistence_runtime",
        forbidden_factory,
    )
    application = build_test_app(DATABASE_ENABLED=False)

    run_lifespan(
        application,
        lambda: assert_noop_persistence(application),
    )
    assert_noop_persistence(application)


def test_enabled_database_is_installed_only_inside_lifespan_and_disposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = RecordingRuntime()
    monkeypatch.setattr(
        app_module,
        "create_sqlalchemy_persistence_runtime",
        lambda _settings: runtime,
    )
    application = build_test_app(
        DATABASE_ENABLED=True,
        DATABASE_URL=DATABASE_URL,
    )

    assert_noop_persistence(application)

    def assert_runtime_is_active() -> None:
        assert application.state.generation_persistence is runtime.persistence
        assert runtime.startup_calls == 1
        assert runtime.close_calls == 0

    run_lifespan(application, assert_runtime_is_active)

    assert_noop_persistence(application)
    assert runtime.close_calls == 1


def test_explicit_persistence_injection_wins_and_is_not_disposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    injected = NoOpGenerationPersistence()

    def forbidden_factory(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("the application must not replace an injected adapter")

    monkeypatch.setattr(
        app_module,
        "create_sqlalchemy_persistence_runtime",
        forbidden_factory,
    )
    application = build_test_app(
        generation_persistence=injected,
        DATABASE_ENABLED=True,
        DATABASE_URL=DATABASE_URL,
    )

    run_lifespan(
        application,
        lambda: assert_injected_persistence(application, injected),
    )
    assert_injected_persistence(application, injected)


def test_startup_failure_is_fixed_detail_and_disposes_owned_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    startup_error = DatabaseStartupError()
    runtime = RecordingRuntime(startup_error=startup_error)
    monkeypatch.setattr(
        app_module,
        "create_sqlalchemy_persistence_runtime",
        lambda _settings: runtime,
    )
    application = build_test_app(
        DATABASE_ENABLED=True,
        DATABASE_URL=DATABASE_URL,
    )

    async def exercise() -> None:
        with pytest.raises(DatabaseStartupError) as caught:
            async with application.router.lifespan_context(application):
                raise AssertionError("startup failure must prevent app startup")
        assert str(caught.value) == "database startup verification failed"
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None

    asyncio.run(exercise())

    assert runtime.startup_calls == 1
    assert runtime.close_calls == 1
    assert_noop_persistence(application)


def test_shutdown_failure_does_not_replace_the_primary_startup_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_shutdown_detail = "private-shutdown-detail"
    runtime = RecordingRuntime(
        startup_error=DatabaseStartupError(),
        close_error=RuntimeError(private_shutdown_detail),
    )
    monkeypatch.setattr(
        app_module,
        "create_sqlalchemy_persistence_runtime",
        lambda _settings: runtime,
    )
    application = build_test_app(
        DATABASE_ENABLED=True,
        DATABASE_URL=DATABASE_URL,
    )

    async def exercise() -> None:
        with pytest.raises(DatabaseStartupError):
            async with application.router.lifespan_context(application):
                raise AssertionError("startup must not succeed")

    asyncio.run(exercise())

    assert runtime.close_calls == 1
    assert "Database runtime shutdown failed type=RuntimeError" in caplog.text
    assert private_shutdown_detail not in caplog.text


def test_runtime_factory_sets_bounded_and_private_engine_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class EngineDouble:
        def dispose(self) -> None:
            return None

    def capture_engine(database_url: Any, **kwargs: Any) -> Engine:
        captured["drivername"] = database_url.drivername
        captured["username"] = database_url.username
        captured["database"] = database_url.database
        captured["url_repr"] = repr(database_url)
        captured["kwargs"] = kwargs
        return cast(Engine, EngineDouble())

    monkeypatch.setattr(runtime_module, "create_engine", capture_engine)
    settings = build_test_app(
        DATABASE_ENABLED=True,
        DATABASE_URL=DATABASE_URL,
    ).state.settings

    runtime = create_sqlalchemy_persistence_runtime(settings)

    assert isinstance(runtime.persistence, SQLAlchemyGenerationPersistence)
    assert captured["drivername"] == "mysql+pymysql"
    assert captured["username"] == "xhs_app"
    assert captured["database"] == "xhs_ai_test"
    assert "test-only-password" not in captured["url_repr"]
    assert "***" in captured["url_repr"]
    options = captured["kwargs"]
    assert options["echo"] is False
    assert options["echo_pool"] is False
    assert options["hide_parameters"] is True
    assert options["pool_pre_ping"] is True
    assert options["pool_size"] == 5
    assert options["max_overflow"] == 5
    assert options["pool_timeout"] == 5
    assert options["pool_recycle"] == 1800
    assert options["pool_reset_on_return"] == "rollback"
    assert options["pool_use_lifo"] is True
    assert options["connect_args"] == {
        "charset": "utf8mb4",
        "connect_timeout": 5,
        "read_timeout": 30,
        "write_timeout": 30,
        "local_infile": False,
    }
    assert "test-only-password" not in repr(captured)


def test_runtime_factory_shares_one_session_factory_and_limiter_with_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EngineDouble:
        def dispose(self) -> None:
            return None

    fake_engine = cast(Engine, EngineDouble())
    monkeypatch.setattr(
        runtime_module,
        "create_engine",
        lambda *_args, **_kwargs: fake_engine,
    )
    settings = build_test_app(
        DATABASE_ENABLED=True,
        DATABASE_URL=DATABASE_URL,
    ).state.settings

    runtime = create_sqlalchemy_persistence_runtime(
        settings,
        require_generation=True,
        require_auth=True,
    )

    assert isinstance(runtime.auth_store, SQLAlchemyAuthStore)
    assert (
        runtime.persistence._session_factory
        is runtime.auth_store._session_factory
    )
    assert runtime.persistence._limiter is runtime.auth_store._limiter
    assert runtime.persistence._limiter is runtime._limiter


def test_remote_runtime_enables_certificate_and_identity_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ca_path = tmp_path / "mysql-ca.pem"
    ca_path.write_text("test-only-ca", encoding="utf-8")
    captured: dict[str, Any] = {}

    class EngineDouble:
        def dispose(self) -> None:
            return None

    def capture_engine(_database_url: Any, **kwargs: Any) -> Engine:
        captured.update(kwargs)
        return cast(Engine, EngineDouble())

    monkeypatch.setattr(runtime_module, "create_engine", capture_engine)
    settings = build_test_app(
        DATABASE_ENABLED=True,
        DATABASE_URL=(
            "mysql+pymysql://xhs_app:test-only-password@"
            "db.example.com:3306/xhs_ai_test"
        ),
        DATABASE_TLS_CA=str(ca_path),
    ).state.settings

    create_sqlalchemy_persistence_runtime(settings)

    assert captured["connect_args"]["ssl_ca"] == str(ca_path)
    assert captured["connect_args"]["ssl_verify_cert"] is True
    assert captured["connect_args"]["ssl_verify_identity"] is True


def test_runtime_factory_erases_engine_construction_error_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_detail = "private-database-construction-detail"

    def fail_engine(*_args: object, **_kwargs: object) -> Engine:
        raise RuntimeError(private_detail)

    monkeypatch.setattr(runtime_module, "create_engine", fail_engine)
    settings = build_test_app(
        DATABASE_ENABLED=True,
        DATABASE_URL=DATABASE_URL,
    ).state.settings

    with pytest.raises(DatabaseStartupError) as caught:
        create_sqlalchemy_persistence_runtime(settings)

    assert str(caught.value) == "database startup verification failed"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert private_detail not in repr(caught.value)


def test_runtime_factory_disposes_engine_if_later_assembly_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispose_calls: list[str] = []

    class EngineDouble:
        def dispose(self) -> None:
            dispose_calls.append("disposed")

    fake_engine = cast(Engine, EngineDouble())
    monkeypatch.setattr(
        runtime_module,
        "create_engine",
        lambda *_args, **_kwargs: fake_engine,
    )
    monkeypatch.setattr(
        runtime_module,
        "sessionmaker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("private-session-factory-detail")
        ),
    )
    settings = build_test_app(
        DATABASE_ENABLED=True,
        DATABASE_URL=DATABASE_URL,
    ).state.settings

    with pytest.raises(DatabaseStartupError) as caught:
        create_sqlalchemy_persistence_runtime(settings)

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert dispose_calls == ["disposed"]


def test_runtime_probe_erases_connection_error_context() -> None:
    private_detail = "private-database-connection-detail"

    class FailingEngine:
        def connect(self) -> None:
            raise RuntimeError(private_detail)

        def dispose(self) -> None:
            return None

    limiter = CapacityLimiter(1)
    fake_engine = cast(Engine, FailingEngine())
    factory = sessionmaker(bind=fake_engine, class_=Session)
    runtime = SQLAlchemyPersistenceRuntime(
        fake_engine,
        SQLAlchemyGenerationPersistence(factory, limiter=limiter),
        limiter=limiter,
    )

    async def exercise() -> DatabaseStartupError:
        with pytest.raises(DatabaseStartupError) as caught:
            await runtime.startup()
        await runtime.aclose()
        return caught.value

    error = asyncio.run(exercise())

    assert str(error) == "database startup verification failed"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert private_detail not in repr(error)


def test_runtime_close_waits_for_capacity_even_when_cancelled() -> None:
    dispose_calls: list[str] = []

    class EngineDouble:
        def dispose(self) -> None:
            dispose_calls.append("disposed")

    limiter = CapacityLimiter(1)
    fake_engine = cast(Engine, EngineDouble())
    factory = sessionmaker(bind=fake_engine, class_=Session)
    runtime = SQLAlchemyPersistenceRuntime(
        fake_engine,
        SQLAlchemyGenerationPersistence(factory, limiter=limiter),
        limiter=limiter,
    )

    async def exercise() -> None:
        holder = object()
        await limiter.acquire_on_behalf_of(holder)
        close_task = asyncio.create_task(runtime.aclose())
        await asyncio.sleep(0)
        close_task.cancel()
        limiter.release_on_behalf_of(holder)
        try:
            await close_task
        except asyncio.CancelledError:
            pass
        await runtime.aclose()

    asyncio.run(exercise())

    assert dispose_calls == ["disposed"]


def test_runtime_close_erases_dispose_error_context() -> None:
    private_detail = "private-engine-dispose-detail"

    class FailingEngine:
        def dispose(self) -> None:
            raise RuntimeError(private_detail)

    limiter = CapacityLimiter(1)
    fake_engine = cast(Engine, FailingEngine())
    factory = sessionmaker(bind=fake_engine, class_=Session)
    runtime = SQLAlchemyPersistenceRuntime(
        fake_engine,
        SQLAlchemyGenerationPersistence(factory, limiter=limiter),
        limiter=limiter,
    )

    async def exercise() -> DatabaseShutdownError:
        with pytest.raises(DatabaseShutdownError) as caught:
            await runtime.aclose()
        return caught.value

    error = asyncio.run(exercise())

    assert str(error) == "database shutdown failed"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert private_detail not in repr(error)


def test_runtime_probe_requires_a_preexisting_table_and_never_creates_it(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    engine = create_engine(
        f"sqlite+pysqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=Session,
    )
    limiter = CapacityLimiter(1)
    adapter = SQLAlchemyGenerationPersistence(factory, limiter=limiter)
    runtime = SQLAlchemyPersistenceRuntime(
        engine,
        adapter,
        limiter=limiter,
    )

    async def rejected_before_schema_exists() -> DatabaseStartupError:
        with pytest.raises(DatabaseStartupError) as caught:
            await runtime.startup()
        await runtime.aclose()
        return caught.value

    startup_error = asyncio.run(rejected_before_schema_exists())

    assert startup_error.__cause__ is None
    assert startup_error.__context__ is None

    verification_engine = create_engine(
        f"sqlite+pysqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    try:
        assert "generation_records" not in set(
            inspect(verification_engine).get_table_names()
        )
        Base.metadata.create_all(verification_engine)
    finally:
        verification_engine.dispose()

    second_engine = create_engine(
        f"sqlite+pysqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    second_factory = sessionmaker(
        bind=second_engine,
        expire_on_commit=False,
        class_=Session,
    )
    second_limiter = CapacityLimiter(1)
    second_runtime = SQLAlchemyPersistenceRuntime(
        second_engine,
        SQLAlchemyGenerationPersistence(
            second_factory,
            limiter=second_limiter,
        ),
        limiter=second_limiter,
    )

    async def accepted_after_schema_exists() -> None:
        await second_runtime.startup()
        await second_runtime.aclose()
        await second_runtime.aclose()

    asyncio.run(accepted_after_schema_exists())


def test_generation_only_probe_requires_owner_column_but_not_auth_tables(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'generation-only.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE generation_records ("
            "id INTEGER PRIMARY KEY, user_id INTEGER NULL, "
            "image_preview BLOB NULL, "
            "image_preview_media_type TEXT NULL, "
            "deleted_at TEXT NULL, "
            "risk_assessment JSON NULL)"
        )
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    limiter = CapacityLimiter(1)
    runtime = SQLAlchemyPersistenceRuntime(
        engine,
        SQLAlchemyGenerationPersistence(factory, limiter=limiter),
        limiter=limiter,
        require_generation=True,
        require_auth=False,
    )

    async def exercise() -> None:
        await runtime.startup()
        await runtime.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "missing_column",
    [
        "image_preview",
        "image_preview_media_type",
        "deleted_at",
        "risk_assessment",
    ],
)
def test_generation_probe_rejects_each_missing_history_lifecycle_column(
    tmp_path: Path,
    missing_column: str,
) -> None:
    columns = {
        "image_preview": "image_preview BLOB NULL",
        "image_preview_media_type": "image_preview_media_type TEXT NULL",
        "deleted_at": "deleted_at TEXT NULL",
        "risk_assessment": "risk_assessment JSON NULL",
    }
    selected = [
        definition
        for name, definition in columns.items()
        if name != missing_column
    ]
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / f'missing-{missing_column}.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE generation_records ("
            "id INTEGER PRIMARY KEY, user_id INTEGER NULL, "
            + ", ".join(selected)
            + ")"
        )
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    limiter = CapacityLimiter(1)
    runtime = SQLAlchemyPersistenceRuntime(
        engine,
        SQLAlchemyGenerationPersistence(factory, limiter=limiter),
        limiter=limiter,
    )

    async def exercise() -> None:
        with pytest.raises(DatabaseStartupError):
            await runtime.startup()
        await runtime.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "column_name,column_type",
    [
        ("image_preview", "BLOB"),
        ("image_preview_media_type", "TEXT"),
        ("deleted_at", "TEXT"),
        ("risk_assessment", "JSON"),
    ],
)
def test_generation_probe_rejects_non_nullable_history_lifecycle_columns(
    tmp_path: Path,
    column_name: str,
    column_type: str,
) -> None:
    definitions = {
        "image_preview": "image_preview BLOB NULL",
        "image_preview_media_type": "image_preview_media_type TEXT NULL",
        "deleted_at": "deleted_at TEXT NULL",
        "risk_assessment": "risk_assessment JSON NULL",
    }
    definitions[column_name] = f"{column_name} {column_type} NOT NULL"
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / f'non-null-{column_name}.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE generation_records ("
            "id INTEGER PRIMARY KEY, user_id INTEGER NULL, "
            + ", ".join(definitions.values())
            + ")"
        )
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    limiter = CapacityLimiter(1)
    runtime = SQLAlchemyPersistenceRuntime(
        engine,
        SQLAlchemyGenerationPersistence(factory, limiter=limiter),
        limiter=limiter,
    )

    async def exercise() -> None:
        with pytest.raises(DatabaseStartupError):
            await runtime.startup()
        await runtime.aclose()

    asyncio.run(exercise())


def test_generation_probe_rejects_non_json_risk_snapshot_column(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'risk-snapshot-text.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE generation_records ("
            "id INTEGER PRIMARY KEY, user_id INTEGER NULL, "
            "image_preview BLOB NULL, "
            "image_preview_media_type TEXT NULL, "
            "deleted_at TEXT NULL, "
            "risk_assessment TEXT NULL)"
        )
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    limiter = CapacityLimiter(1)
    runtime = SQLAlchemyPersistenceRuntime(
        engine,
        SQLAlchemyGenerationPersistence(factory, limiter=limiter),
        limiter=limiter,
    )

    async def exercise() -> None:
        with pytest.raises(DatabaseStartupError):
            await runtime.startup()
        await runtime.aclose()

    asyncio.run(exercise())


def test_generation_only_probe_rejects_the_pre_ownership_schema(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'pre-ownership.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE generation_records (id INTEGER PRIMARY KEY)"
        )
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    limiter = CapacityLimiter(1)
    runtime = SQLAlchemyPersistenceRuntime(
        engine,
        SQLAlchemyGenerationPersistence(factory, limiter=limiter),
        limiter=limiter,
        require_generation=True,
        require_auth=False,
    )

    async def exercise() -> DatabaseStartupError:
        with pytest.raises(DatabaseStartupError) as caught:
            await runtime.startup()
        await runtime.aclose()
        return caught.value

    error = asyncio.run(exercise())

    assert str(error) == "database startup verification failed"
    assert error.__cause__ is None
    assert error.__context__ is None


def test_generation_only_probe_rejects_a_non_nullable_owner_column(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'non-null-owner.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE generation_records ("
            "id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL)"
        )
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    limiter = CapacityLimiter(1)
    runtime = SQLAlchemyPersistenceRuntime(
        engine,
        SQLAlchemyGenerationPersistence(factory, limiter=limiter),
        limiter=limiter,
        require_generation=True,
        require_auth=False,
    )

    async def exercise() -> DatabaseStartupError:
        with pytest.raises(DatabaseStartupError) as caught:
            await runtime.startup()
        await runtime.aclose()
        return caught.value

    error = asyncio.run(exercise())

    assert str(error) == "database startup verification failed"
    assert error.__cause__ is None
    assert error.__context__ is None


def test_auth_probe_accepts_complete_ownership_schema_when_adapter_is_injected(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'auth-only.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    limiter = CapacityLimiter(1)
    auth_store = SQLAlchemyAuthStore(factory, limiter=limiter)
    runtime = SQLAlchemyPersistenceRuntime(
        engine,
        SQLAlchemyGenerationPersistence(factory, limiter=limiter),
        limiter=limiter,
        auth_store=auth_store,
        require_generation=False,
        require_auth=True,
    )

    async def exercise() -> None:
        await runtime.startup()
        await runtime.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "schema_defect",
    [
        "missing_owner_column",
        "missing_owner_foreign_key",
        "missing_owner_index",
        "wrong_owner_index_order",
    ],
)
def test_auth_probe_rejects_incomplete_generation_ownership(
    tmp_path: Path,
    schema_defect: str,
) -> None:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / f'unsafe-owner-{schema_defect}.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    owner_column = (
        "" if schema_defect == "missing_owner_column" else "user_id INTEGER,"
    )
    owner_foreign_key = (
        ""
        if schema_defect in {"missing_owner_column", "missing_owner_foreign_key"}
        else ", FOREIGN KEY (user_id) REFERENCES users (id)"
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_active INTEGER NOT NULL,
                email_verified_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE auth_sessions (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                token_hash BLOB NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
            """
        )
        connection.exec_driver_sql(
            f"""
            CREATE TABLE generation_records (
                id INTEGER PRIMARY KEY,
                {owner_column}
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
                {owner_foreign_key}
            )
            """
        )
        if schema_defect not in {
            "missing_owner_column",
            "missing_owner_index",
        }:
            index_columns = (
                "status, user_id, created_at"
                if schema_defect == "wrong_owner_index_order"
                else "user_id, status, created_at"
            )
            connection.exec_driver_sql(
                "CREATE INDEX ix_generation_records_owner_test "
                f"ON generation_records ({index_columns})"
            )

    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    limiter = CapacityLimiter(1)
    runtime = SQLAlchemyPersistenceRuntime(
        engine,
        SQLAlchemyGenerationPersistence(factory, limiter=limiter),
        limiter=limiter,
        auth_store=SQLAlchemyAuthStore(factory, limiter=limiter),
        require_generation=False,
        require_auth=True,
    )

    async def rejected() -> DatabaseStartupError:
        with pytest.raises(DatabaseStartupError) as caught:
            await runtime.startup()
        await runtime.aclose()
        return caught.value

    error = asyncio.run(rejected())

    assert str(error) == "database startup verification failed"
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.parametrize("missing_table", ["users", "auth_sessions"])
def test_auth_probe_rejects_each_missing_auth_table(
    tmp_path: Path,
    missing_table: str,
) -> None:
    database_path = tmp_path / f"missing-{missing_table}.sqlite3"
    engine = create_engine(
        f"sqlite+pysqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    GenerationRecord.__table__.create(engine)
    if missing_table == "auth_sessions":
        User.__table__.create(engine)
    elif missing_table == "users":
        # SQLite permits creating the child table before its referenced table,
        # which lets this test isolate the missing parent-table probe.
        AuthSession.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    limiter = CapacityLimiter(1)
    auth_store = SQLAlchemyAuthStore(factory, limiter=limiter)
    runtime = SQLAlchemyPersistenceRuntime(
        engine,
        SQLAlchemyGenerationPersistence(factory, limiter=limiter),
        limiter=limiter,
        auth_store=auth_store,
        require_generation=True,
        require_auth=True,
    )

    async def exercise() -> DatabaseStartupError:
        with pytest.raises(DatabaseStartupError) as caught:
            await runtime.startup()
        await runtime.aclose()
        return caught.value

    error = asyncio.run(exercise())

    assert str(error) == "database startup verification failed"
    assert error.__cause__ is None
    assert error.__context__ is None


def test_auth_probe_never_creates_missing_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "no-auth-ddl.sqlite3"
    engine = create_engine(
        f"sqlite+pysqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    GenerationRecord.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    limiter = CapacityLimiter(1)
    runtime = SQLAlchemyPersistenceRuntime(
        engine,
        SQLAlchemyGenerationPersistence(factory, limiter=limiter),
        limiter=limiter,
        auth_store=SQLAlchemyAuthStore(factory, limiter=limiter),
        require_generation=True,
        require_auth=True,
    )

    async def rejected() -> None:
        with pytest.raises(DatabaseStartupError):
            await runtime.startup()
        await runtime.aclose()

    asyncio.run(rejected())

    verification_engine = create_engine(
        f"sqlite+pysqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    try:
        assert set(inspect(verification_engine).get_table_names()) == {
            "generation_records"
        }
    finally:
        verification_engine.dispose()


@pytest.mark.parametrize(
    "schema_defect",
    [
        "missing_user_column",
        "missing_session_column",
        "email_not_unique",
        "token_hash_not_unique",
        "missing_user_foreign_key",
    ],
)
def test_auth_probe_rejects_incomplete_or_unsafe_table_shells(
    tmp_path: Path,
    schema_defect: str,
) -> None:
    database_path = tmp_path / f"unsafe-auth-{schema_defect}.sqlite3"
    engine = create_engine(
        f"sqlite+pysqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    user_password_column = (
        "" if schema_defect == "missing_user_column" else "password_hash TEXT NOT NULL,"
    )
    email_unique = "" if schema_defect == "email_not_unique" else "UNIQUE"
    session_expiry_column = (
        "" if schema_defect == "missing_session_column" else "expires_at TEXT NOT NULL,"
    )
    token_unique = "" if schema_defect == "token_hash_not_unique" else "UNIQUE"
    user_foreign_key = (
        ""
        if schema_defect == "missing_user_foreign_key"
        else ", FOREIGN KEY (user_id) REFERENCES users (id)"
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL {email_unique},
                {user_password_column}
                is_active INTEGER NOT NULL,
                email_verified_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE generation_records (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE INDEX ix_generation_records_user_status_created
            ON generation_records (user_id, status, created_at)
            """
        )
        connection.exec_driver_sql(
            f"""
            CREATE TABLE auth_sessions (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                token_hash BLOB NOT NULL {token_unique},
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                {session_expiry_column}
                revoked_at TEXT
                {user_foreign_key}
            )
            """
        )
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    limiter = CapacityLimiter(1)
    runtime = SQLAlchemyPersistenceRuntime(
        engine,
        SQLAlchemyGenerationPersistence(factory, limiter=limiter),
        limiter=limiter,
        auth_store=SQLAlchemyAuthStore(factory, limiter=limiter),
        require_generation=False,
        require_auth=True,
    )

    async def rejected() -> DatabaseStartupError:
        with pytest.raises(DatabaseStartupError) as caught:
            await runtime.startup()
        await runtime.aclose()
        return caught.value

    error = asyncio.run(rejected())

    assert str(error) == "database startup verification failed"
    assert error.__cause__ is None
    assert error.__context__ is None


def test_mysql_migration_is_scoped_to_one_preselected_database() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "001_generation_records.sql"
    ).read_text(encoding="utf-8")
    executable_sql = "\n".join(
        line for line in migration.splitlines() if not line.lstrip().startswith("--")
    )
    statements = [statement.strip() for statement in executable_sql.split(";")]
    statements = [statement for statement in statements if statement]

    assert len(statements) == 1
    statement = statements[0].upper()
    assert statement.startswith("CREATE TABLE GENERATION_RECORDS")
    assert "ENGINE=INNODB" in statement
    assert "CHARACTER SET=UTF8MB4" in statement
    assert statement.count("COLLATE ASCII_BIN") == 3
    assert re.search(
        r"\b(DROP|TRUNCATE|DELETE|UPDATE|ALTER|RENAME|GRANT|USE)\b"
        r"|\bCREATE\s+DATABASE\b",
        statement,
    ) is None


def assert_noop_persistence(application: Any) -> None:
    assert isinstance(
        application.state.generation_persistence,
        NoOpGenerationPersistence,
    )


def assert_injected_persistence(
    application: Any,
    expected: GenerationPersistence,
) -> None:
    assert application.state.generation_persistence is expected
