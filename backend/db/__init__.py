"""Backend database package（FastAPI + 纯 SQLAlchemy 2.x）。

核心导出：
    # ---- 基础对象 ----
    Base              : SQLAlchemy 2.x DeclarativeBase（所有 ORM 模型继承它）
    GenerationRecord  : ORM 模型，表 generation_records
    TASK_STATUS_PENDING / TASK_STATUS_SUCCESS / TASK_STATUS_FAILED

    # ---- Engine / Session 工厂 ----
    make_engine(settings, /)           : 从 Settings 构造 SQLAlchemy Engine（连接池）
    make_session_factory(engine, /)    : 从 Engine 构造 sessionmaker(bind=engine)
    init_database(settings, engine=None)
                                       : 幂等初始化：MySQL 时先建库，再 metadata.create_all(engine)

    # ---- FastAPI 依赖注入（Depends） ----
    get_session_factory(settings)  -> sessionmaker
    get_db(session_factory)        -> Generator[Session, None, None]
                                       : 标准 FastAPI yield Session 依赖

    # ---- 给成员 B 的稳定 Repository API（第一个参数都是显式 Session） ----
    create_pending(db, /, image_path=None, user_input=None, image_description=None)
    mark_success(db, /, task_id, title, content, tags, image_description=None)
    mark_failed(db, /, task_id, error_code, error_message, image_description=None)
    get_record(db, /, task_id)
    list_records(db, /, status=None, limit=100)

    # ---- 便捷版（当你已经用 Settings / 默认 Settings 初始化了全局单例） ----
    # 下面每个函数都和上面签名一样，只是省略第一个 db 参数；内部用 global _global_session_factory
    # 注意：必须先调用 init_database(settings) 或 set_global_session_factory(...) 才能用这些便捷版。
    init_database_global(settings, engine=None)
    set_global_session_factory(session_factory)
    close_global_engine_session()
    create_pending_g(...)
    mark_success_g(...)
    mark_failed_g(...)
    get_record_g(...)
    list_records_g(...)

为什么第一个参数都是显式 Session：
    这样 FastAPI 路由层可以统一由 Depends(get_db) 注入同一个 session 完成事务，
    也方便测试时自行构造 session（用临时 SQLite、临时 MySQL schema），
    完全不依赖 Flask / app context，纯 SQLAlchemy 2.x 用法。

校验规则：
    mark_success() 在写入前强制调用 validate_copy()（标题≤20 / 正文非空 / 描述非空 /
    标签 3~5 个 去重补 #），不合规直接抛 BusinessException(VALIDATION_ERROR)，
    不写脏数据到数据库。
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import (
    Any,
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
    )
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "缺少依赖：SQLAlchemy>=2.0, PyMySQL>=1.1。请先运行：pip install -r requirements.txt"
    ) from e


from ..schemas import BusinessException, ErrorCode
from ..validation import validate_copy
from .config import Settings, get_settings, mask_database_url, parse_mysql_url


TASK_STATUS_PENDING = "pending"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_FAILED = "failed"


class Base(DeclarativeBase):
    """SQLAlchemy 2.x Declarative Base。用 Base.metadata.create_all(engine) 建表。"""

    type_annotation_map = {
        Dict[str, Any]: JSON,
        List[str]: JSON,
    }


class GenerationRecord(Base):
    __tablename__ = "generation_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TASK_STATUS_PENDING)

    image_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True, default=None)
    image_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    user_input: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)

    title: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default=None)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    tags: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True, default=None)

    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, default=None)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)

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
            "user_input": self.user_input,
            "title": self.title,
            "content": self.content,
            "tags": list(self.tags) if self.tags is not None else None,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ---------------------------------------------------------------------------
# Engine / Session factory helpers
# ---------------------------------------------------------------------------

def make_engine(settings: Settings, /) -> Any:
    """根据 Settings 构造 SQLAlchemy Engine。

    - SQLite：加 ?check_same_thread=False，保证并行安全
    - MySQL：启用 utf8mb4，设置 pool_recycle=3600，避免长连接被 MySQL server 断开
    """
    url = settings.DATABASE_URL or ""
    masked_url = mask_database_url(url)
    connect_args: Dict[str, Any] = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        return create_engine(url, connect_args=connect_args, future=True)
    return create_engine(
        url,
        future=True,
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args={"charset": "utf8mb4", **connect_args} if url.startswith("mysql") else connect_args,
        echo=False,
    )


def make_session_factory(engine: Any, /) -> sessionmaker[Session]:
    """从 engine 构造标准 sessionmaker（expire_on_commit=False 方便 to_dict() 后继续用对象）。"""
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        future=True,
        class_=Session,
    )


def _create_mysql_database_if_missing(url: str) -> None:
    """MySQL 专用：在 SQLAlchemy create_all 之前，用 PyMySQL 先建库（保证库存在）。"""
    parsed = parse_mysql_url(url)
    if not parsed:
        return
    user, password, host, port, database = parsed
    try:
        import pymysql  # type: ignore
    except ImportError:  # pragma: no cover
        return
    try:
        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password or "",
            charset="utf8mb4",
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
    except Exception as e:  # pragma: no cover - 真实连接失败交给上层报错
        raise BusinessException(
            ErrorCode.DATABASE_ERROR,
            f"创建 MySQL 数据库失败：{e}（连接信息 user={user} host={host} port={port} db={database}）",
        )


def init_database(settings: Settings, engine: Any = None) -> Tuple[Any, sessionmaker[Session]]:
    """幂等初始化数据库：建库（MySQL 时） + 建表。

    返回 (engine, session_factory)。
    """
    url = settings.DATABASE_URL or ""
    if url.startswith("mysql"):
        _create_mysql_database_if_missing(url)
    if engine is None:
        engine = make_engine(settings)
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    return engine, session_factory


# ---------------------------------------------------------------------------
# FastAPI 依赖：Depends(get_db)
# ---------------------------------------------------------------------------

_global_engine = None
_global_session_factory: Optional[sessionmaker[Session]] = None


def set_global_session_factory(session_factory: sessionmaker[Session], /) -> None:
    """（给便捷版 API 用）设置全局 session factory。"""
    global _global_session_factory
    _global_session_factory = session_factory


def init_database_global(settings: Optional[Settings] = None, engine: Any = None) -> sessionmaker[Session]:
    """便捷版初始化：构造 engine + session_factory 并存为全局单例。返回 session_factory。"""
    global _global_engine, _global_session_factory
    settings = settings or get_settings()
    _global_engine, sf = init_database(settings, engine)
    _global_session_factory = sf
    return sf


def close_global_engine_session() -> None:
    """全局资源清理（测试/进程退出时调用）。"""
    global _global_engine, _global_session_factory
    if _global_session_factory is not None:
        try:
            from sqlalchemy.orm import close_all_sessions
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


def get_session_factory(settings: Optional[Settings] = None, /) -> sessionmaker[Session]:
    """FastAPI Depends：返回全局 session_factory（未初始化则懒初始化一次）。"""
    if _global_session_factory is None:
        init_database_global(settings or get_settings())
    assert _global_session_factory is not None
    return _global_session_factory


def get_db(
    session_factory: Optional[sessionmaker[Session]] = None,
    /,
) -> Generator[Session, None, None]:
    """FastAPI 标准 session 依赖（yield Session，finally 自动 close）。

    FastAPI 路由层用法：
        from fastapi import Depends, APIRouter
        from sqlalchemy.orm import Session
        from backend.db import get_db, create_pending

        router = APIRouter()

        @router.post("/generations")
        def create_task(db: Session = Depends(get_db)):
            rec = create_pending(db, image_path=..., user_input=...)
            return {"generation_id": rec.task_id}
    """
    factory = session_factory or get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def new_session(*, session_factory: Optional[sessionmaker[Session]] = None) -> Generator[Session, None, None]:
    """非 FastAPI 场景（脚本/CLI/测试）上下文管理器用法，保证 commit/rollback/close。"""
    factory = session_factory or get_session_factory()
    s = factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Repository API（显式 Session 参数，推荐）
# ---------------------------------------------------------------------------

def _generate_task_id() -> str:
    return "gen_" + uuid.uuid4().hex[:24]


def create_pending(
    db: Session,
    /,
    *,
    image_path: Optional[str] = None,
    user_input: Optional[str] = None,
    image_description: Optional[str] = None,
) -> GenerationRecord:
    """[供 B 调用] 创建 pending 任务并落库，返回 ORM 对象。"""
    task_id = _generate_task_id()
    record = GenerationRecord(
        task_id=task_id,
        status=TASK_STATUS_PENDING,
        image_path=image_path,
        user_input=user_input,
        image_description=image_description,
    )
    try:
        db.add(record)
        db.flush()
        db.commit()
        db.refresh(record)
        return record
    except BusinessException:
        raise
    except Exception as e:
        raise BusinessException(ErrorCode.DATABASE_ERROR, f"create_pending failed: {e}")


def _get_by_task_id_or_raise(db: Session, task_id: str) -> GenerationRecord:
    from sqlalchemy import select as _sa_select

    row = db.execute(
        _sa_select(GenerationRecord).where(GenerationRecord.task_id == task_id)
    ).scalar_one_or_none()
    if row is None:
        raise BusinessException(ErrorCode.TASK_NOT_FOUND, f"generation_id={task_id} not found")
    return row


def mark_success(
    db: Session,
    /,
    *,
    task_id: str,
    title: str,
    content: str,
    tags: Iterable[str],
    image_description: Optional[str] = None,
) -> GenerationRecord:
    """[供 B 调用] 标记 success 并落库。

    内部强制跑 validate_copy()：不合规直接抛 BusinessException(VALIDATION_ERROR)，
    不写脏数据。所有写库字段都会用 validate_copy 规范化后的值。
    """
    record = _get_by_task_id_or_raise(db, task_id)
    desc_to_validate = image_description if image_description is not None else (record.image_description or "")
    norm_tags_list = list(tags) if not isinstance(tags, list) else tags
    norm_desc, norm_title, norm_content, norm_tags = validate_copy(
        desc_to_validate, title, content, norm_tags_list
    )

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
        raise
    except Exception as e:
        raise BusinessException(ErrorCode.DATABASE_ERROR, f"mark_success failed: {e}")


def mark_failed(
    db: Session,
    /,
    *,
    task_id: str,
    error_code: str,
    error_message: str,
    image_description: Optional[str] = None,
) -> GenerationRecord:
    """[供 B 调用] 标记 failed 并落库，写入 error_code + error_message。"""
    record = _get_by_task_id_or_raise(db, task_id)
    record.status = TASK_STATUS_FAILED
    record.error_code = error_code
    record.error_message = error_message
    if image_description is not None:
        record.image_description = image_description
    record.updated_at = datetime.utcnow()

    try:
        db.flush()
        db.commit()
        db.refresh(record)
        return record
    except BusinessException:
        raise
    except Exception as e:
        raise BusinessException(ErrorCode.DATABASE_ERROR, f"mark_failed failed: {e}")


def get_record(db: Session, /, *, task_id: str) -> Optional[GenerationRecord]:
    from sqlalchemy import select as _sa_select

    return db.execute(
        _sa_select(GenerationRecord).where(GenerationRecord.task_id == task_id)
    ).scalar_one_or_none()


def list_records(
    db: Session,
    /,
    *,
    status: Optional[str] = None,
    limit: int = 100,
) -> List[GenerationRecord]:
    from sqlalchemy import select as _sa_select

    stmt = _sa_select(GenerationRecord)
    if status:
        stmt = stmt.where(GenerationRecord.status == status)
    stmt = stmt.order_by(GenerationRecord.created_at.desc()).limit(limit)
    return list(db.execute(stmt).scalars().all())


# ---------------------------------------------------------------------------
# 便捷版 Repository API（隐式使用全局 session_factory）
# ---------------------------------------------------------------------------

def _g_session() -> Session:
    if _global_session_factory is None:
        init_database_global(get_settings())
    assert _global_session_factory is not None
    return _global_session_factory()


def _wrap_with_session(func, *args, **kwargs):
    s = _g_session()
    try:
        return func(s, *args, **kwargs)
    finally:
        try:
            s.close()
        except Exception:
            pass


def create_pending_g(**kwargs) -> GenerationRecord:
    return _wrap_with_session(create_pending, **kwargs)


def mark_success_g(**kwargs) -> GenerationRecord:
    return _wrap_with_session(mark_success, **kwargs)


def mark_failed_g(**kwargs) -> GenerationRecord:
    return _wrap_with_session(mark_failed, **kwargs)


def get_record_g(**kwargs) -> Optional[GenerationRecord]:
    return _wrap_with_session(get_record, **kwargs)


def list_records_g(**kwargs) -> List[GenerationRecord]:
    return _wrap_with_session(list_records, **kwargs)


__all__ = [
    "Base",
    "GenerationRecord",
    "TASK_STATUS_PENDING",
    "TASK_STATUS_SUCCESS",
    "TASK_STATUS_FAILED",
    "make_engine",
    "make_session_factory",
    "init_database",
    "init_database_global",
    "set_global_session_factory",
    "close_global_engine_session",
    "get_session_factory",
    "get_db",
    "new_session",
    "create_pending",
    "mark_success",
    "mark_failed",
    "get_record",
    "list_records",
    "create_pending_g",
    "mark_success_g",
    "mark_failed_g",
    "get_record_g",
    "list_records_g",
]
