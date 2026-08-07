"""Backend database package（FastAPI + 纯 SQLAlchemy 2.x 同步模式）。

统一配置源：backend.core.config（单一 Settings，禁止第二套）。

同步 FastAPI 无参数依赖（B 的 async 接口建议后续用 starlette.concurrency.run_in_threadpool / asyncio.to_thread 把同步操作放进线程池执行，
不要直接把阻塞 Session 放进协程）：

    from backend.db import get_db, create_pending, mark_success, mark_failed, get_record
    from fastapi import Depends

    @router.post("/generations")
    def create_task(db: Session = Depends(get_db)):
        r = create_pending(db, image_summary="...", ...)
        return r.to_dict()

字段映射（B 已与 A 对齐，统一双写）：
    image_summary  <-> DB image_description
    body           <-> DB content
    Repository / 校验 / to_dict 都同时接受两套字段名并在输出中同时包含两套名字。

generation_id（task_id）：
    只生成一次 uuid.uuid4()（B 的标准 UUID），写库失败时不再重复生成（直接抛异常）。

写库函数异常处理：
    所有路径（含查询阶段）统一包裹在 try/except 中；
    遇到 BusinessException 以外的异常先 rollback()，再转换为 DATABASE_ERROR，
    防止密码 / 原始 SQL / 原始驱动堆栈泄漏到上层响应。
"""
from __future__ import annotations

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
        close_all_sessions,
    )
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "缺少依赖：SQLAlchemy>=2.0.25, PyMySQL>=1.1。请先运行：pip install -r requirements.txt"
    ) from e

from ..core.config import Settings, build_database_url, get_settings, mask_database_url, parse_mysql_url
from ..schemas import BusinessException, ErrorCode
from ..validation import validate_copy


TASK_STATUS_PENDING = "pending"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_FAILED = "failed"


# ---------------------------------------------------------------------------
# ORM Model
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
# Engine / Session helpers（同步，无 async driver 依赖）
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


def _safe_database_name(database: str) -> str:
    """MySQL 数据库名严格白名单：字母数字下划线，字母开头，长度 <=64，且不含 prod/online/production。"""
    import re as _re
    if not database or not isinstance(database, str):
        return ""
    if not _re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", database):
        return ""
    low = database.lower()
    for token in ("prod", "online", "production", "master", "live", "release"):
        if token in low:
            return ""
    return database


def _create_mysql_database_if_missing(url: str) -> None:
    parsed = parse_mysql_url(url)
    if not parsed:
        return
    user, password, host, port, database = parsed
    if not _safe_database_name(database):
        raise BusinessException(
            ErrorCode.DATABASE_ERROR,
            f"非法 MySQL 数据库名：{database!r}（仅允许字母数字下划线，字母开头，<=64 字符，禁止 prod/online/production 等关键词）",
        )
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
            try:
                conn.close()
            except Exception:
                pass
    except BusinessException:
        raise
    except Exception as e:
        safe_info = (
            f"创建 MySQL 数据库失败（user={user} host={mask_database_url_part(host)} port={port} db={database}）"
        )
        raise BusinessException(ErrorCode.DATABASE_ERROR, safe_info + f": {type(e).__name__}")


def init_database(
    settings: Optional[Settings] = None, engine: Any = None
) -> Tuple[Any, sessionmaker[Session]]:
    settings = settings or get_settings()
    url = build_database_url(settings)
    if url.startswith("mysql"):
        _create_mysql_database_if_missing(url)
    if engine is None:
        engine = make_engine(settings)
    try:
        Base.metadata.create_all(engine)
    except Exception as e:
        raise BusinessException(
            ErrorCode.DATABASE_ERROR,
            f"初始化数据表失败：{type(e).__name__}",
        )
    sf = make_session_factory(engine)
    return engine, sf


# ---------------------------------------------------------------------------
# 全局单例 & FastAPI 同步依赖（get_db 无参数，直接 Depends(get_db)）
# ---------------------------------------------------------------------------

_global_engine = None
_global_session_factory: Optional[sessionmaker[Session]] = None


def get_session_factory() -> sessionmaker[Session]:
    """Depends(get_db) 内部使用：懒初始化一次全局 session_factory。"""
    global _global_engine, _global_session_factory
    if _global_session_factory is None:
        _global_engine, sf = init_database(get_settings())
        _global_session_factory = sf
    assert _global_session_factory is not None
    return _global_session_factory


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


def get_db() -> Generator[Session, None, None]:
    """FastAPI 无参数同步依赖：

        def create_task(db: Session = Depends(get_db)): ...
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        try:
            session.commit()
        except Exception as e:
            try:
                session.rollback()
            except Exception:
                pass
            raise BusinessException(
                ErrorCode.DATABASE_ERROR,
                f"提交事务失败：{type(e).__name__}",
            )
    except BusinessException:
        try:
            session.rollback()
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            session.rollback()
        except Exception:
            pass
        raise BusinessException(
            ErrorCode.DATABASE_ERROR,
            f"数据库会话失败：{type(e).__name__}",
        )
    finally:
        try:
            session.close()
        except Exception:
            pass


@contextmanager
def new_session(*, session_factory: Optional[sessionmaker[Session]] = None) -> Generator[Session, None, None]:
    """脚本/测试用同步上下文管理器（不需要 Depends 时）。"""
    factory = session_factory or get_session_factory()
    s = factory()
    try:
        yield s
        try:
            s.commit()
        except Exception as e:
            try:
                s.rollback()
            except Exception:
                pass
            raise BusinessException(
                ErrorCode.DATABASE_ERROR,
                f"提交事务失败：{type(e).__name__}",
            )
    except BusinessException:
        try:
            s.rollback()
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            s.rollback()
        except Exception:
            pass
        raise BusinessException(
            ErrorCode.DATABASE_ERROR,
            f"数据库会话失败：{type(e).__name__}",
        )
    finally:
        try:
            s.close()
        except Exception:
            pass


def mask_database_url_part(host_or_user: Any) -> str:
    s = str(host_or_user or "")
    if len(s) <= 2:
        return "*" * len(s)
    return s[0] + "*" * (len(s) - 2) + s[-1]


# ---------------------------------------------------------------------------
# Repository API（纯同步）
# ---------------------------------------------------------------------------

def _generate_task_id() -> str:
    """B 的标准 UUID：只生成一次 uuid.uuid4()，写入 DB 前不再重新生成。"""
    return str(uuid.uuid4())


def _resolve_image_description(
    *, image_description: Any = None, image_summary: Any = None
) -> Optional[str]:
    # B 已对齐的字段：image_summary 优先（A 对齐），否则 image_description
    if image_summary is not None:
        v = str(image_summary)
        if v == "None":
            return None
        return v
    if image_description is not None:
        v = str(image_description)
        if v == "None":
            return None
        return v
    return None


def _resolve_content(content: Any = None, body: Any = None) -> Optional[str]:
    if body is not None:
        v = str(body)
        if v == "None":
            return None
        return v
    if content is not None:
        v = str(content)
        if v == "None":
            return None
        return v
    return None


def create_pending(
    db: Session,
    /,
    *,
    image_path: Optional[str] = None,
    user_input: Optional[str] = None,
    image_description: Optional[str] = None,
    image_summary: Optional[str] = None,
) -> GenerationRecord:
    """创建 pending 任务。接受 image_summary（A 对齐字段）或 image_description。

    task_id 只生成一次 uuid.uuid4()（标准 UUID4），写库失败不再重新生成，直接抛异常。
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
        raise BusinessException(
            ErrorCode.DATABASE_ERROR,
            f"create_pending 失败：{type(e).__name__}",
        )


def _get_by_task_id_or_raise(db: Session, task_id: str) -> GenerationRecord:
    from sqlalchemy import select as _sa_select

    try:
        row = db.execute(
            _sa_select(GenerationRecord).where(GenerationRecord.task_id == task_id)
        ).scalar_one_or_none()
    except BusinessException:
        raise
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        raise BusinessException(
            ErrorCode.DATABASE_ERROR,
            f"查询任务失败：{type(e).__name__}",
        )
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
    """标记 success。支持 image_summary / body 别名。内部强制 validate_copy，不合规 rollback。

    查询 / 校验 / flush 全路径都做了 rollback + DATABASE_ERROR 转换，防止事务泄漏和原始错误外泄。
    """
    try:
        record = _get_by_task_id_or_raise(db, task_id)
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
        raise BusinessException(
            ErrorCode.DATABASE_ERROR,
            f"mark_success 失败：{type(e).__name__}",
        )


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
    """标记 failed。接受 image_summary 别名。查询 / flush 全路径做 rollback + DATABASE_ERROR 转换。"""
    try:
        record = _get_by_task_id_or_raise(db, task_id)
        record.status = TASK_STATUS_FAILED
        record.error_code = error_code
        record.error_message = error_message
        img_desc = _resolve_image_description(image_description=image_description, image_summary=image_summary)
        if img_desc is not None:
            record.image_description = img_desc
        record.updated_at = datetime.utcnow()

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
        raise BusinessException(
            ErrorCode.DATABASE_ERROR,
            f"mark_failed 失败：{type(e).__name__}",
        )


def get_record(db: Session, /, *, task_id: str) -> Optional[GenerationRecord]:
    from sqlalchemy import select as _sa_select
    try:
        return db.execute(
            _sa_select(GenerationRecord).where(GenerationRecord.task_id == task_id)
        ).scalar_one_or_none()
    except BusinessException:
        raise
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        raise BusinessException(
            ErrorCode.DATABASE_ERROR,
            f"get_record 失败：{type(e).__name__}",
        )


def list_records(
    db: Session, /, *, status: Optional[str] = None, limit: int = 100
) -> List[GenerationRecord]:
    from sqlalchemy import select as _sa_select
    try:
        stmt = _sa_select(GenerationRecord).order_by(GenerationRecord.created_at.desc())
        if status is not None:
            stmt = stmt.where(GenerationRecord.status == status)
        stmt = stmt.limit(max(1, min(int(limit), 500)))
        return list(db.execute(stmt).scalars().all())
    except BusinessException:
        raise
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        raise BusinessException(
            ErrorCode.DATABASE_ERROR,
            f"list_records 失败：{type(e).__name__}",
        )


# 兼容简写（B 之前用 xxx_g 作“全局 session”的便捷函数，但这里保持同步语义即可）
def create_pending_g(**kwargs) -> GenerationRecord:
    with new_session() as s:
        return create_pending(s, **kwargs)


def mark_success_g(**kwargs) -> GenerationRecord:
    with new_session() as s:
        return mark_success(s, **kwargs)


def mark_failed_g(**kwargs) -> GenerationRecord:
    with new_session() as s:
        return mark_failed(s, **kwargs)


__all__ = [
    # status
    "TASK_STATUS_PENDING",
    "TASK_STATUS_SUCCESS",
    "TASK_STATUS_FAILED",
    # model
    "Base",
    "GenerationRecord",
    # engine & session
    "make_engine",
    "make_session_factory",
    "init_database",
    "init_database_global",
    "get_session_factory",
    "set_global_session_factory",
    "close_global_engine_session",
    "get_db",
    "new_session",
    # helpers
    "mask_database_url_part",
    "_safe_database_name",
    # repo
    "create_pending",
    "mark_success",
    "mark_failed",
    "get_record",
    "list_records",
    # compat
    "create_pending_g",
    "mark_success_g",
    "mark_failed_g",
]
