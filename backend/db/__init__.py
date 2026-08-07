"""Backend database package.

导出：
- db: SQLAlchemy 扩展实例
- init_database(app): 绑定到 Flask app 并建表（可重复执行）
- GenerationRecord, TASK_STATUS_PENDING / SUCCESS / FAILED
- create_pending / mark_success / mark_failed / get_record / list_records
"""
import os
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List

from flask_sqlalchemy import SQLAlchemy

from ..schemas import ErrorCode, BusinessException
from ..validation import validate_copy

db = SQLAlchemy()


TASK_STATUS_PENDING = "pending"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_FAILED = "failed"


class GenerationRecord(db.Model):
    """persistence table for generation tasks (MySQL / SQLite compatible)."""

    __tablename__ = "generation_records"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # generation_id: 对外暴露的任务唯一 ID，成员 B 通过此字段追踪
    task_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    status = db.Column(db.String(16), nullable=False, default=TASK_STATUS_PENDING, index=True)

    image_path = db.Column(db.String(512), nullable=True)
    image_description = db.Column(db.Text, nullable=True)
    user_input = db.Column(db.Text, nullable=True)

    title = db.Column(db.String(100), nullable=True)
    content = db.Column(db.Text, nullable=True)
    tags = db.Column(db.JSON, nullable=True)

    error_code = db.Column(db.String(64), nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

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
            "tags": self.tags,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


def init_database(app) -> None:
    """将 SQLAlchemy 绑定到 Flask app 并建表（幂等，可重复执行）。"""
    db.init_app(app)
    with app.app_context():
        db.create_all()


def _generate_task_id() -> str:
    return "gen_" + uuid.uuid4().hex[:24]


def create_pending(
    image_path: Optional[str] = None,
    user_input: Optional[str] = None,
    image_description: Optional[str] = None,
) -> GenerationRecord:
    """[供 B 调用] 创建 pending 任务并落库，返回对象。"""
    task_id = _generate_task_id()
    record = GenerationRecord(
        task_id=task_id,
        status=TASK_STATUS_PENDING,
        image_path=image_path,
        user_input=user_input,
        image_description=image_description,
    )
    try:
        db.session.add(record)
        db.session.commit()
        db.session.refresh(record)
        return record
    except Exception as e:
        db.session.rollback()
        raise BusinessException(ErrorCode.DATABASE_ERROR, f"create_pending failed: {e}")


def mark_success(
    task_id: str,
    title: str,
    content: str,
    tags: List[str],
    image_description: Optional[str] = None,
) -> GenerationRecord:
    """[供 B 调用] 标记成功并落库。内部强制跑 validate_copy，不合规直接抛异常不写库。"""
    record = GenerationRecord.query.filter_by(task_id=task_id).first()
    if not record:
        raise BusinessException(ErrorCode.TASK_NOT_FOUND, f"generation_id={task_id} not found")

    desc_to_validate = image_description if image_description is not None else (record.image_description or "")
    norm_desc, norm_title, norm_content, norm_tags = validate_copy(
        desc_to_validate, title, content, tags
    )

    record.status = TASK_STATUS_SUCCESS
    record.title = norm_title
    record.content = norm_content
    record.tags = norm_tags
    record.image_description = norm_desc
    record.updated_at = datetime.utcnow()
    record.error_code = None
    record.error_message = None

    try:
        db.session.commit()
        db.session.refresh(record)
        return record
    except Exception as e:
        db.session.rollback()
        raise BusinessException(ErrorCode.DATABASE_ERROR, f"mark_success failed: {e}")


def mark_failed(
    task_id: str,
    error_code: str,
    error_message: str,
    image_description: Optional[str] = None,
) -> GenerationRecord:
    """[供 B 调用] 标记失败并落库，写入错误码+错误信息。"""
    record = GenerationRecord.query.filter_by(task_id=task_id).first()
    if not record:
        raise BusinessException(ErrorCode.TASK_NOT_FOUND, f"generation_id={task_id} not found")

    record.status = TASK_STATUS_FAILED
    record.error_code = error_code
    record.error_message = error_message
    if image_description is not None:
        record.image_description = image_description
    record.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        db.session.refresh(record)
        return record
    except Exception as e:
        db.session.rollback()
        raise BusinessException(ErrorCode.DATABASE_ERROR, f"mark_failed failed: {e}")


def get_record(task_id: str) -> Optional[GenerationRecord]:
    return GenerationRecord.query.filter_by(task_id=task_id).first()


def list_records(status: Optional[str] = None, limit: int = 100) -> List[GenerationRecord]:
    q = GenerationRecord.query
    if status:
        q = q.filter_by(status=status)
    return q.order_by(GenerationRecord.created_at.desc()).limit(limit).all()
