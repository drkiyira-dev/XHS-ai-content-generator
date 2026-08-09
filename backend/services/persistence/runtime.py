"""Application-owned SQLAlchemy runtime for explicit MySQL persistence."""

from anyio import CancelScope, CapacityLimiter, Lock, to_thread
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from backend.core.config import Settings
from backend.db.models import GenerationRecord
from backend.services.persistence.sqlalchemy import (
    SQLAlchemyGenerationPersistence,
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
    ) -> None:
        self._engine = engine
        self._persistence = persistence
        self._limiter = limiter
        self._closed = False
        self._close_lock = Lock()

    @property
    def persistence(self) -> SQLAlchemyGenerationPersistence:
        """Return the adapter installed into FastAPI application state."""
        return self._persistence

    async def startup(self) -> None:
        """Verify connectivity and the pre-provisioned table without writing DDL."""
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
                verified = inspect(connection).has_table(
                    GenerationRecord.__tablename__
                )
        except Exception:
            verified = False
        if not verified:
            raise DatabaseStartupError()


def create_sqlalchemy_persistence_runtime(
    settings: Settings,
) -> SQLAlchemyPersistenceRuntime:
    """Build a bounded MySQL runtime without opening a connection or running DDL."""
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
        runtime = SQLAlchemyPersistenceRuntime(
            engine,
            persistence,
            limiter=limiter,
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
