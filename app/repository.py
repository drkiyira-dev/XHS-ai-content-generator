"""数据访问层：提供 create_pending / mark_success / mark_failed 三个方法。

成员 B 可以直接调用这些方法而无需写 SQL。
"""
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List

from app import db
from app.models import (
    GenerationRecord,
    TASK_STATUS_PENDING,
    TASK_STATUS_SUCCESS,
    TASK_STATUS_FAILED,
)
from app.schemas import ErrorCode, BusinessException
from app.validators import validate_copy


def _generate_task_id() -> str:
    return "task_" + uuid.uuid4().hex[:24]


def create_pending(
    image_path: Optional[str] = None,
    user_input: Optional[str] = None,
    image_description: Optional[str] = None,
) -> GenerationRecord:
    """创建一条 pending 状态的任务记录并返回。

    不写任何 SQL，底层由 SQLAlchemy 自动完成。
    """
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
        raise BusinessException(ErrorCode.DATABASE_ERROR, f"创建任务失败: {str(e)}")


def mark_success(
    task_id: str,
    title: str,
    content: str,
    tags: List[str],
    image_description: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> GenerationRecord:
    """将任务标记为 success，并写入标题、正文、标签等结果。

    强制调用文案校验：标题≤20字、正文非空、描述非空、标签3~5个且去重补#。
    不合规直接抛 BusinessException，不会写入成功记录。
    """
    record = GenerationRecord.query.filter_by(task_id=task_id).first()
    if not record:
        raise BusinessException(ErrorCode.TASK_NOT_FOUND, f"task_id={task_id} 不存在")

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
        raise BusinessException(ErrorCode.DATABASE_ERROR, f"更新成功状态失败: {str(e)}")


def mark_failed(
    task_id: str,
    error_code: str,
    error_message: str,
    image_description: Optional[str] = None,
) -> GenerationRecord:
    """将任务标记为 failed，并记录错误码和错误信息。"""
    record = GenerationRecord.query.filter_by(task_id=task_id).first()
    if not record:
        raise BusinessException(ErrorCode.TASK_NOT_FOUND, f"task_id={task_id} 不存在")

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
        raise BusinessException(ErrorCode.DATABASE_ERROR, f"更新失败状态失败: {str(e)}")


def get_record(task_id: str) -> Optional[GenerationRecord]:
    return GenerationRecord.query.filter_by(task_id=task_id).first()


def list_records(status: Optional[str] = None, limit: int = 100) -> List[GenerationRecord]:
    q = GenerationRecord.query
    if status:
        q = q.filter_by(status=status)
    return q.order_by(GenerationRecord.created_at.desc()).limit(limit).all()
