"""Backend database package（FastAPI + 纯 SQLAlchemy 2.x 同步/异步双模式。

统一配置源：backend/config.py（单一 Settings，禁止第二套）。

同步（B 的同步路由 / CLI / 测试）：
    from backend.db import get_db, create_pending, mark_success, mark_failed
    from fastapi import Depends
    @router.post("/generations")
    def create_task(db: Session = Depends(get_db)): ...

异步（B 的 async 路由）：
    from backend.db import async_get_db, acreate_pending, amark_success, amark_failed
    @router.post("/generations")
    async def create_task(db: AsyncSession = Depends(async_get_db)): ...

字段映射（B 已与 A 对齐）：
    image_summary  -> DB image_description
    body           -> DB content
    两个别名在 Repository 层都接受。

generation_id（task_id）：
    只生成一次 uuid.uuid4()（B 的标准 UUID），写入 DB 前不会重复生成。

写库函数异常：先 rollback()，再抛 BusinessException（避免事务泄漏。
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager, asynccontextmanager
from datetime import datetime
from typing import (
    Any,
    AsyncGenerator,
    Dict,
    Generator,
    Iterable,
    List,
    Optional,
    Tuple,
)

try:
    from sqlalchemy import (
        JSON,
        DateTime,
        Integer,
        String,
        Text,
        create_engine,
        func,
        Index,
    )
    from sqlalchemy.orm import (
        DeclarativeBase,
        Mapped,
        Session,
        mapped_column,
        sessionmaker,
        close_all_sessions,
    )
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "缺少依赖：SQLAlchemy>=2.0.25, PyMySQL>=1.1。请先运行：pip install -r requirements.txt"
    ) from e

try:
    from sqlalchemy.ext.asyncio import (
        AsyncEngine,
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
except ImportError:  # pragma: no cover - 同步模式不一定需要 async extra
    AsyncEngine = None  # type: ignore
    AsyncSession = None  # type: ignore
    async_sessionmaker = None  # type: ignore
    create_async_engine = None  # type: ignore

from ..config import Settings, build_database_url, get_settings, mask_database_url, parse_mysql_url
from ..schemas import BusinessException, ErrorCode
from ..validation import validate_copy


TASK_STATUS_PENDING = "pending"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_FAILED = "failed"


# ---------------------------------------------------------------------------
# ORM Model（同步 & 异步共用）
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    type_annotation_map = {
        Dict[str, Any]: JSON,
        List[str]: JSON,
    }


class GenerationRecord(Base):
    __tablename__ = "generation_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TASK_STATUS_PENDING)

    image_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    image_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_input: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    title: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)

    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=datetime.utcnow,
        default=datetime.utcnow,
    )

    __table_args__ = (
        Index("idx_task_id", "task_id", unique=True),
        Index("idx_status", "status"),
        Index("idx_created_at", "created_at"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "generation_id": self.task_id,
            "task_id": self.task_id,
            "status": self.status,
            "image_path": self.image_path,
            "image_description": self.image_description,
            "image_summary": self.image_description,
            "user_input": self.user_input,
            "title": self.title,
            "content": self.content,
            "body": self.content,
            "tags": list(self.tags) if self.tags is not None else None,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ---------------------------------------------------------------------------
# Engine / Session helpers（同步）
# ---------------------------------------------------------------------------

def make_engine(settings: Settings, /) -> Any:
    url = build_database_url(settings)
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False}, future=True)
    return create_engine(
        url,
        future=True,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=False,
    )


def make_session_factory(engine: Any, /) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        future=True,
        class_=Session,
    )


def _create_mysql_database_if_missing(url: str) -> None:
    parsed = parse_mysql_url(url)
    if not parsed:
        return
    user, password, host, port, database = parsed
    try:
        import pymysql  # type: ignore
    except ImportError:
        return
    try:
        conn = pymysql.connect(
            host=host, port=port, user=user, password=password or "", charset="utf8mb4"
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{database}` "
                    f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        raise BusinessException(
            ErrorCode.DATABASE_ERROR,
            f"创建 MySQL 数据库失败：{e}（user={user} host={host} port={port} db={database}）",
        )


def init_database(
    settings: Optional[Settings] = None, engine: Any = None
) -> Tuple[Any, sessionmaker[Session]]:
    settings = settings or get_settings()
    url = build_database_url(settings)
    if url.startswith("mysql"):
        _create_mysql_database_if_missing(url)
    if engine is None:
        engine = make_engine(settings)
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)
    return engine, sf


# ---------------------------------------------------------------------------
# 全局单例 & FastAPI 同步依赖（get_db 无参数，直接 Depends(get_db)）
# ---------------------------------------------------------------------------

_global_engine = None
_global_session_factory: Optional[sessionmaker[Session]] = None


def set_global_session_factory(sf: sessionmaker[Session], /) -> None:
    global _global_session_factory
    _global_session_factory = sf


def init_database_global(settings: Optional[Settings] = None, engine: Any = None) -> sessionmaker[Session]:
    global _global_engine, _global_session_factory
    settings = settings or get_settings()
    _global_engine, sf = init_database(settings, engine)
    _global_session_factory = sf
    return sf


def close_global_engine_session() -> None:
    global _global_engine, _global_session_factory
    try:
        close_all_sessions()
    except Exception:
        pass
    if _global_engine is not None:
        try:
            _global_engine.dispose()
        except Exception:
            pass
    _global_engine = None
    _global_session_factory = None


def get_session_factory() -> sessionmaker[Session]:
    """给 Depends 内部调用：懒初始化一次全局 session_factory。"""
    if _global_session_factory is None:
        init_database_global(get_settings())
    assert _global_session_factory is not None
    return _global_session_factory


def get_db() -> Generator[Session, None, None]:
    """FastAPI 无参数同步依赖：
    def create_task(db: Session = Depends(get_db)): ...
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            session.close()
        except Exception:
            pass


@contextmanager
def new_session(*, session_factory: Optional[sessionmaker[Session]] = None) -> Generator[Session, None, None]:
    """脚本/测试用同步上下文管理器。"""
    factory = session_factory or get_session_factory()
    s = factory()
    try:
        yield s
        s.commit()
    except Exception:
        try:
            s.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            s.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 异步 asyncio 版本（给 B 的 async 路由用，线程隔离或直接 async_session）
# ---------------------------------------------------------------------------

_async_engine: Any = None
_async_session_factory: Any = None


def _async_sync_url_to_async(url: str) -> str:
    """把 mysql+pymysql://... -> mysql+aiomysql://...（异步 driver 替换。"""
    if url.startswith("mysql+pymysql"):
        return url.replace("mysql+pymysql", "mysql+aiomysql", 1)
    if url.startswith("postgresql+psycopg2"):
        return url.replace("postgresql+psycopg2", "postgresql+asyncpg", 1)
    if url.startswith("sqlite"):
        # 不强制：同步和异步都能用
        return url
    return url


def make_async_engine(settings: Settings, /) -> Any:
    if create_async_engine is None:  # pragma: no cover
        raise RuntimeError("SQLAlchemy async 不可用，请 pip install sqlalchemy[asyncio] + aiomysql/asyncpg")
    url = _async_sync_url_to_async(build_database_url(settings))
    if url.startswith("sqlite"):
        return create_async_engine(url, future=True)
    return create_async_engine(
        url,
        future=True,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=False,
    )


def init_database_async(settings: Optional[Settings] = None) -> Tuple[Any, Any]:
    settings = settings or get_settings()
    # 先同步建库建表（metadata.create_all 同步即可，无需 async）
    sync_engine, _ = init_database(settings)
    try:
        sync_engine.dispose()
    except Exception:
        pass
    aengine = make_async_engine(settings)
    asf = async_sessionmaker(
        bind=aengine,
        autoflush=False,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    global _async_engine, _async_session_factory
    _async_engine = aengine
    _async_session_factory = asf
    return aengine, asf


def _get_async_session_factory() -> Any:
    if _async_session_factory is None:
        init_database_async(get_settings())
    return _async_session_factory


@asynccontextmanager
async def async_get_db() -> AsyncGenerator[Any, None, None]:
    """FastAPI 无参数异步依赖：
    async def create_task(db: AsyncSession = Depends(async_get_db)): ...
    """
    factory = _get_async_session_factory()
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        try:
            await session.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            await session.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 同步 Repository API（第一个参数显式 Session；异常时先 rollback）
# ---------------------------------------------------------------------------

def _generate_task_id() -> str:
    """B 的标准 UUID：只生成一次 uuid.uuid4()，写入 DB 前不再重新生成。"""
    return str(uuid.uuid4())


def _resolve_image_description(image_description: Any = None, image_summary: Any = None) -> Optional[str]:
    if image_summary is not None:
        return None if image_summary is None else str(image_summary)
    return None if image_description is None else str(image_description)


def _resolve_content(content: Any = None, body: Any = None) -> Optional[str]:
    if body is not None:
        return None if body is None else str(body)
    return None if content is None else str(content)


def create_pending(
    db: Session,
    /,
    *,
    image_path: Optional[str] = None,
    user_input: Optional[str] = None,
    image_description: Optional[str] = None,
    image_summary: Optional[str] = None,
) -> GenerationRecord:
    """[B 调用] 创建 pending 任务。接受 image_summary（A 对齐字段）或 image_description。

    task_id 只生成一次 uuid.uuid4()（标准 UUID），写库失败时不再重复生成（抛异常给上层）。
    """
    task_id = _generate_task_id()  # 只生成一次
    img_desc = _resolve_image_description(image_description=image_description, image_summary=image_summary)
    record = GenerationRecord(
        task_id=task_id,
        status=TASK_STATUS_PENDING,
        image_path=image_path,
        user_input=user_input,
        image_description=img_desc,
    )
    try:
        db.add(record)
        db.flush()
        db.commit()
        db.refresh(record)
        return record
    except BusinessException:
        try:
            db.rollback()
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        raise BusinessException(ErrorCode.DATABASE_ERROR, f"create_pending failed: {e}")


def _get_by_task_id_or_raise(db: Session, task_id: str) -> GenerationRecord:
    from sqlalchemy import select as _sa_select

    row = db.execute(_sa_select(GenerationRecord).where(GenerationRecord.task_id == task_id)).scalar_one_or_none()
    if row is None:
        raise BusinessException(ErrorCode.TASK_NOT_FOUND, f"task_id={task_id} 不存在")
    return row


def mark_success(
    db: Session,
    /,
    *,
    task_id: str,
    title: str,
    content: Optional[str] = None,
    tags: Iterable[str],
    image_description: Optional[str] = None,
    image_summary: Optional[str] = None,
    body: Optional[str] = None,
) -> GenerationRecord:
    """[B 调用] 标记 success。支持 image_summary / body 别名。内部强制 validate_copy，不合规 rollback。"""
    record = _get_by_task_id_or_raise(db, task_id)
    # 校验时：显式传参优先于 DB 已有，否则 DB 已有
    desc_arg = _resolve_image_description(image_description=image_description, image_summary=image_summary)
    desc_to_validate = desc_arg if desc_arg is not None else (record.image_description or "")
    content_arg = _resolve_content(content=content, body=body)
    content_to_validate = content_arg if content_arg is not None else (record.content or "")
    try:
        norm_desc, norm_title, norm_content, norm_tags = validate_copy(
            image_description=desc_to_validate,
            title=title,
            content=content_to_validate,
            tags=list(tags),
        )
    except BusinessException:
        try:
            db.rollback()
        except Exception:
            pass
        raise

    record.status = TASK_STATUS_SUCCESS
    record.title = norm_title
    record.content = norm_content
    record.tags = list(norm_tags)
    record.image_description = norm_desc
    record.updated_at = datetime.utcnow()
    record.error_code = None
    record.error_message = None

    try:
        db.flush()
        db.commit()
        db.refresh(record)
        return record
    except BusinessException:
        try:
            db.rollback()
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        raise BusinessException(ErrorCode.DATABASE_ERROR, f"mark_success failed: {e}")


def mark_failed(
    db: Session,
    /,
    *,
    task_id: str,
    error_code: str,
    error_message: str,
    image_description: Optional[str] = None,
    image_summary: Optional[str] = None,
) -> GenerationRecord:
    """[B 调用] 标记 failed。接受 image_summary 别名。异常时先 rollback。"""
    record = _get_by_task_id_or_raise(db, task_id)
    record.status = TASK_STATUS_FAILED
    record.error_code = error_code
    record.error_message = error_message
    img_desc = _resolve_image_description(image_description=image_description, image_summary=image_summary)
    if img_desc is not None:
        record.image_description = img_desc
    record.updated_at = datetime.utcnow()

    try:
        db.flush()
        db.commit()
        db.refresh(record)
        return record
    except BusinessException:
        try:
            db.rollback()
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        raise BusinessException(ErrorCode.DATABASE_ERROR, f"mark_failed failed: {e}")


def get_record(db: Session, /, *, task_id: str) -> Optional[GenerationRecord]:
    from sqlalchemy import select as _sa_select

    return db.execute(_sa_select(GenerationRecord).where(GenerationRecord.task_id == task_id)).scalar_one_or_none()


def list_records(
    db: Session, /, *, status: Optional[str] = None, limit: int = 100
) -> List[GenerationRecord]:
    from sqlalchemy import select as _sa_select

    stmt = _sa_select(GenerationRecord)
    if status:
        stmt = stmt.where(GenerationRecord.status == status)
    stmt = stmt.order_by(GenerationRecord.created_at.desc()).limit(limit)
    return list(db.execute(stmt).scalars().all())


# ---------------------------------------------------------------------------
# 异步 Repository API（第一个参数显式 AsyncSession；await 调用）
# ---------------------------------------------------------------------------

async def acreate_pending(
    db: Any,
    /,
    *,
    image_path: Optional[str] = None,
    user_input: Optional[str] = None,
    image_description: Optional[str] = None,
    image_summary: Optional[str] = None,
) -> GenerationRecord:
    task_id = _generate_task_id()
    img_desc = _resolve_image_description(image_description=image_description, image_summary=image_summary)
    record = GenerationRecord(
        task_id=task_id,
        status=TASK_STATUS_PENDING,
        image_path=image_path,
        user_input=user_input,
        image_description=img_desc,
    )
    try:
        db.add(record)
        await db.flush()
        await db.commit()
        await db.refresh(record)
        return record
    except BusinessException:
        try:
            await db.rollback()
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            await db.rollback()
        except Exception:
            pass
        raise BusinessException(ErrorCode.DATABASE_ERROR, f"acreate_pending failed: {e}")


async def _a_get_by_task_id_or_raise(db: Any, task_id: str) -> GenerationRecord:
    from sqlalchemy import select as _sa_select

    row = (await db.execute(_sa_select(GenerationRecord).where(GenerationRecord.task_id == task_id))).scalar_one_or_none()
    if row is None:
        raise BusinessException(ErrorCode.TASK_NOT_FOUND, f"task_id={task_id} 不存在")
    return row


async def amark_success(
    db: Any,
    /,
    *,
    task_id: str,
    title: str,
    content: Optional[str] = None,
    tags: Iterable[str],
    image_description: Optional[str] = None,
    image_summary: Optional[str] = None,
    body: Optional[str] = None,
) -> GenerationRecord:
    record = await _a_get_by_task_id_or_raise(db, task_id)
    desc_arg = _resolve_image_description(image_description=image_description, image_summary=image_summary)
    desc_to_validate = desc_arg if desc_arg is not None else (record.image_description or "")
    content_arg = _resolve_content(content=content, body=body)
    content_to_validate = content_arg if content_arg is not None else (record.content or "")
    try:
        norm_desc, norm_title, norm_content, norm_tags = validate_copy(
            image_description=desc_to_validate,
            title=title,
            content=content_to_validate,
            tags=list(tags),
        )
    except BusinessException:
        try:
            await db.rollback()
        except Exception:
            pass
        raise

    record.status = TASK_STATUS_SUCCESS
    record.title = norm_title
    record.content = norm_content
    record.tags = list(norm_tags)
    record.image_description = norm_desc
    record.updated_at = datetime.utcnow()
    record.error_code = None
    record.error_message = None

    try:
        await db.flush()
        await db.commit()
        await db.refresh(record)
        return record
    except BusinessException:
        try:
            await db.rollback()
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            await db.rollback()
        except Exception:
            pass
        raise BusinessException(ErrorCode.DATABASE_ERROR, f"amark_success failed: {e}")


async def amark_failed(
    db: Any,
    /,
    *,
    task_id: str,
    error_code: str,
    error_message: str,
    image_description: Optional[str] = None,
    image_summary: Optional[str] = None,
) -> GenerationRecord:
    record = await _a_get_by_task_id_or_raise(db, task_id)
    record.status = TASK_STATUS_FAILED
    record.error_code = error_code
    record.error_message = error_message
    img_desc = _resolve_image_description(image_description=image_description, image_summary=image_summary)
    if img_desc is not None:
        record.image_description = img_desc
    record.updated_at = datetime.utcnow()

    try:
        await db.flush()
        await db.commit()
        await db.refresh(record)
        return record
    except BusinessException:
        try:
            await db.rollback()
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            await db.rollback()
        except Exception:
            pass
        raise BusinessException(ErrorCode.DATABASE_ERROR, f"amark_failed failed: {e}")


async def aget_record(db: Any, /, *, task_id: str) -> Optional[GenerationRecord]:
    from sqlalchemy import select as _sa_select

    return (await db.execute(_sa_select(GenerationRecord).where(GenerationRecord.task_id == task_id))).scalar_one_or_none()


# ---------------------------------------------------------------------------
# 便捷版（无 Session 参数，内部开新 session）
# ---------------------------------------------------------------------------

def _g_sync_session() -> Session:
    return get_session_factory()()


def _wrap_sync(func, **kwargs):
    s = _g_sync_session()
    try:
        return func(s, **kwargs)
    finally:
        try:
            s.close()
        except Exception:
            pass


def create_pending_g(**kwargs) -> GenerationRecord:
    return _wrap_sync(create_pending, **kwargs)

def mark_success_g(**kwargs) -> GenerationRecord:
    return _wrap_sync(mark_success, **kwargs)

def mark_failed_g(**kwargs) -> GenerationRecord:
    return _wrap_sync(mark_failed, **kwargs)

def get_record_g(**kwargs) -> Optional[GenerationRecord]:
    return _wrap_sync(get_record, **kwargs)

def list_records_g(**kwargs) -> List[GenerationRecord]:
    return _wrap_sync(list_records, **kwargs)


__all__ = [
    # ORM
    "Base", "GenerationRecord",
    "TASK_STATUS_PENDING", "TASK_STATUS_SUCCESS", "TASK_STATUS_FAILED",
    # Sync engine/session
    "make_engine", "make_session_factory",
    "init_database", "init_database_global",
    "set_global_session_factory", "close_global_engine_session",
    "get_session_factory",
    "get_db", "new_session",
    # Async engine/session
    "init_database_async", "async_get_db", "make_async_engine",
    # Sync Repository
    "create_pending", "mark_success", "mark_failed", "get_record", "list_records",
    # Async Repository
    "acreate_pending", "amark_success", "amark_failed", "aget_record",
    # Convenience
    "create_pending_g", "mark_success_g", "mark_failed_g", "get_record_g", "list_records_g",
]
