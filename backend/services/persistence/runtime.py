"""Application-owned SQLAlchemy runtime for explicit MySQL persistence."""

from anyio import CancelScope, CapacityLimiter, Lock, to_thread
from sqlalchemy import JSON, create_engine, inspect
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.orm import Session, sessionmaker

from backend.core.config import Settings
from backend.db.models import AuthSession, GenerationRecord, User
from backend.services.auth.sqlalchemy import SQLAlchemyAuthStore
from backend.services.persistence.sqlalchemy import (
    SQLAlchemyGenerationPersistence,
)


_AUTH_REQUIRED_COLUMNS = {
    User.__tablename__: frozenset(
        {
            "id",
            "email",
            "password_hash",
            "is_active",
            "email_verified_at",
            "created_at",
            "updated_at",
            "last_login_at",
        }
    ),
    AuthSession.__tablename__: frozenset(
        {
            "id",
            "user_id",
            "token_hash",
            "created_at",
            "last_seen_at",
            "expires_at",
            "revoked_at",
        }
    ),
}
_GENERATION_REQUIRED_NULLABLE_COLUMNS = frozenset(
    {
        "user_id",
        "image_preview",
        "image_preview_media_type",
        "deleted_at",
        "risk_assessment",
    }
)


class DatabaseStartupError(RuntimeError):
    """Fixed-detail startup failure that cannot expose database information."""

    def __init__(self) -> None:
        super().__init__("database startup verification failed")


class DatabaseShutdownError(RuntimeError):
    """Fixed-detail shutdown failure that cannot expose database information."""

    def __init__(self) -> None:
        super().__init__("database shutdown failed")


class SQLAlchemyPersistenceRuntime:
    """Own one Engine and its B-to-C persistence adapter for an app lifespan."""

    def __init__(
        self,
        engine: Engine,
        persistence: SQLAlchemyGenerationPersistence,
        *,
        limiter: CapacityLimiter,
        auth_store: SQLAlchemyAuthStore | None = None,
        require_generation: bool = True,
        require_auth: bool = False,
    ) -> None:
        self._engine = engine
        self._persistence = persistence
        self._auth_store = auth_store
        self._limiter = limiter
        required_tables: list[str] = []
        if require_generation or require_auth:
            required_tables.append(GenerationRecord.__tablename__)
        if require_auth:
            required_tables.extend(
                (User.__tablename__, AuthSession.__tablename__)
            )
        self._required_tables = tuple(required_tables)
        self._require_generation = require_generation or require_auth
        self._require_auth = require_auth
        self._closed = False
        self._close_lock = Lock()

    @property
    def persistence(self) -> SQLAlchemyGenerationPersistence:
        """Return the adapter installed into FastAPI application state."""
        return self._persistence

    @property
    def auth_store(self) -> SQLAlchemyAuthStore | None:
        """Return the optional account adapter sharing this runtime's pool."""
        return self._auth_store

    async def startup(self) -> None:
        """Verify connectivity and required pre-provisioned tables without DDL."""
        await to_thread.run_sync(
            self._verify_database,
            abandon_on_cancel=False,
            limiter=self._limiter,
        )

    async def aclose(self) -> None:
        """Dispose only the Engine owned by this runtime."""
        with CancelScope(shield=True):
            async with self._close_lock:
                if self._closed:
                    return

                failed = False
                try:
                    await to_thread.run_sync(
                        self._engine.dispose,
                        abandon_on_cancel=False,
                        limiter=self._limiter,
                    )
                except Exception:
                    failed = True
                if failed:
                    raise DatabaseShutdownError()
                self._closed = True

    def _verify_database(self) -> None:
        verified = False
        try:
            with self._engine.connect() as connection:
                inspector = inspect(connection)
                verified = all(
                    inspector.has_table(table_name)
                    for table_name in self._required_tables
                )
                if verified and self._require_generation:
                    verified = _verify_generation_columns(inspector)
                if verified and self._require_auth:
                    verified = (
                        _verify_auth_schema(inspector)
                        and _verify_generation_ownership_schema(inspector)
                    )
        except Exception:
            verified = False
        if not verified:
            raise DatabaseStartupError()


def create_sqlalchemy_persistence_runtime(
    settings: Settings,
    *,
    require_generation: bool = True,
    require_auth: bool = False,
) -> SQLAlchemyPersistenceRuntime:
    """Build shared bounded adapters without connecting or running DDL."""
    engine: Engine | None = None
    runtime: SQLAlchemyPersistenceRuntime | None = None
    try:
        database_url = _validated_database_url(settings)
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

        engine = create_engine(
            database_url,
            connect_args=connect_args,
            echo=False,
            echo_pool=False,
            hide_parameters=True,
            pool_pre_ping=True,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
            pool_recycle=settings.database_pool_recycle_seconds,
            pool_reset_on_return="rollback",
            pool_use_lifo=True,
        )
        session_factory = sessionmaker(
            bind=engine,
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )
        limiter = CapacityLimiter(
            settings.database_pool_size + settings.database_max_overflow
        )
        persistence = SQLAlchemyGenerationPersistence(
            session_factory,
            limiter=limiter,
        )
        auth_store = (
            SQLAlchemyAuthStore(
                session_factory,
                limiter=limiter,
            )
            if require_auth
            else None
        )
        runtime = SQLAlchemyPersistenceRuntime(
            engine,
            persistence,
            limiter=limiter,
            auth_store=auth_store,
            require_generation=require_generation,
            require_auth=require_auth,
        )
    except Exception:
        runtime = None

    if runtime is not None:
        return runtime
    if engine is not None:
        try:
            engine.dispose()
        except Exception:
            pass
    raise DatabaseStartupError()


def _verify_auth_schema(inspector: Inspector) -> bool:
    """Check the minimum account schema required by the auth store."""
    for table_name, required_columns in _AUTH_REQUIRED_COLUMNS.items():
        reflected_columns = {
            column.get("name")
            for column in inspector.get_columns(table_name)
            if isinstance(column, dict)
        }
        if not required_columns.issubset(reflected_columns):
            return False

    if not _has_single_column_unique(inspector, User.__tablename__, "email"):
        return False
    if not _has_single_column_unique(
        inspector,
        AuthSession.__tablename__,
        "token_hash",
    ):
        return False

    for foreign_key in inspector.get_foreign_keys(AuthSession.__tablename__):
        if not isinstance(foreign_key, dict):
            continue
        if (
            tuple(foreign_key.get("constrained_columns") or ()) == ("user_id",)
            and foreign_key.get("referred_table") == User.__tablename__
            and tuple(foreign_key.get("referred_columns") or ()) == ("id",)
        ):
            return True
    return False


def _verify_generation_ownership_schema(inspector: Inspector) -> bool:
    """Require the nullable ownership column, FK, and query index for auth."""
    if not _has_generation_owner_column(inspector):
        return False

    has_owner_foreign_key = any(
        isinstance(foreign_key, dict)
        and tuple(foreign_key.get("constrained_columns") or ()) == ("user_id",)
        and foreign_key.get("referred_table") == User.__tablename__
        and tuple(foreign_key.get("referred_columns") or ()) == ("id",)
        for foreign_key in inspector.get_foreign_keys(
            GenerationRecord.__tablename__
        )
    )
    if not has_owner_foreign_key:
        return False

    return any(
        isinstance(index, dict)
        and tuple(index.get("column_names") or ())
        == ("user_id", "status", "created_at")
        for index in inspector.get_indexes(GenerationRecord.__tablename__)
    )


def _has_generation_owner_column(inspector: Inspector) -> bool:
    """Require the nullable owner column used by the legacy NULL partition."""
    return any(
        isinstance(column, dict)
        and column.get("name") == "user_id"
        and column.get("nullable") is True
        for column in inspector.get_columns(GenerationRecord.__tablename__)
    )


def _verify_generation_columns(inspector: Inspector) -> bool:
    """Require deployed nullable lifecycle columns and a JSON risk snapshot."""
    reflected = {
        column.get("name"): column
        for column in inspector.get_columns(GenerationRecord.__tablename__)
        if isinstance(column, dict)
    }
    nullable_columns_are_valid = all(
        column_name in reflected
        and reflected[column_name].get("nullable") is True
        for column_name in _GENERATION_REQUIRED_NULLABLE_COLUMNS
    )
    return nullable_columns_are_valid and isinstance(
        reflected["risk_assessment"].get("type"),
        JSON,
    )


def _has_single_column_unique(
    inspector: Inspector,
    table_name: str,
    column_name: str,
) -> bool:
    """Recognize one-column uniqueness across database reflection variants."""
    candidates = list(
        inspector.get_unique_constraints(table_name)
    )
    candidates.extend(
        index
        for index in inspector.get_indexes(table_name)
        if isinstance(index, dict) and index.get("unique") in (True, 1)
    )
    return any(
        isinstance(candidate, dict)
        and tuple(candidate.get("column_names") or ()) == (column_name,)
        for candidate in candidates
    )


def _validated_database_url(settings: Settings) -> URL:
    """Parse and authorize one URL without ever returning validation details."""
    database_url: URL | None = None
    raw_url = ""
    try:
        if settings.database_enabled and settings.database_url is not None:
            raw_url = settings.database_url.get_secret_value()
            candidate = make_url(raw_url)
            port = candidate.port
            has_required_parts = bool(
                candidate.host
                and candidate.host.strip()
                and candidate.username
                and candidate.username.strip()
                and candidate.password
                and candidate.password.strip()
                and candidate.database
                and candidate.database.strip()
            )
            is_loopback = bool(
                candidate.host
                and candidate.host.casefold()
                in {"localhost", "127.0.0.1", "::1"}
            )
            tls_is_valid = is_loopback or bool(
                settings.database_tls_ca is not None
                and settings.resolved_database_tls_ca.is_file()
            )
            if (
                candidate.drivername == "mysql+pymysql"
                and has_required_parts
                and candidate.username is not None
                and candidate.username.strip().casefold() != "root"
                and not candidate.query
                and (port is None or 1 <= port <= 65535)
                and tls_is_valid
            ):
                database_url = candidate
    except Exception:
        database_url = None
    finally:
        raw_url = ""

    if database_url is None:
        raise DatabaseStartupError()
    return database_url
