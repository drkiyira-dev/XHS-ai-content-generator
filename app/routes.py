"""API 路由层：只负责调用数据方法和校验方法，不写 SQL。"""
import os
from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename

from app.repository import (
    create_pending,
    mark_success,
    mark_failed,
    get_record,
    list_records,
)
from app.validators import validate_generation_result
from app.schemas import (
    success_response,
    error_response,
    ErrorCode,
    BusinessException,
)

main_bp = Blueprint("main", __name__)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "bmp", "webp"}


def _allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@main_bp.route("/health", methods=["GET"])
def health_check():
    return success_response({"status": "ok"})


@main_bp.route("/api/tasks", methods=["POST"])
def create_task():
    """创建 pending 任务：可选上传图片 + 用户输入。"""
    user_input = request.form.get("user_input", type=str, default=None)
    image_path = None

    if "image" in request.files:
        file = request.files["image"]
        if file and file.filename:
            if not _allowed_file(file.filename):
                return error_response(
                    ErrorCode.PARAMS_ERROR,
                    f"不支持的文件类型，允许: {sorted(ALLOWED_EXTENSIONS)}",
                )
            upload_dir = current_app.config["UPLOAD_DIR"]
            os.makedirs(upload_dir, exist_ok=True)
            filename = secure_filename(file.filename)
            save_path = os.path.join(upload_dir, filename)
            file.save(save_path)
            image_path = save_path

    record = create_pending(image_path=image_path, user_input=user_input)
    return success_response(record.to_dict(), "任务创建成功")


@main_bp.route("/api/tasks/<task_id>/success", methods=["POST"])
def task_success(task_id: str):
    """标记任务成功，请求体需包含 title / content / tags / image_description。"""
    payload = request.get_json(silent=True) or {}
    try:
        normalized = validate_generation_result(payload)
    except BusinessException as e:
        return jsonify(e.to_dict()), 400

    try:
        record = mark_success(
            task_id=task_id,
            title=normalized["title"],
            content=normalized["content"],
            tags=normalized["tags"],
            image_description=normalized["image_description"],
        )
    except BusinessException as e:
        return jsonify(e.to_dict()), 404 if e.code == ErrorCode.TASK_NOT_FOUND else 500

    return success_response(record.to_dict(), "任务已标记成功")


@main_bp.route("/api/tasks/<task_id>/fail", methods=["POST"])
def task_fail(task_id: str):
    """标记任务失败，请求体需包含 error_code / error_message。"""
    payload = request.get_json(silent=True) or {}
    error_code = payload.get("error_code") or ErrorCode.LLM_GENERATE_ERROR
    error_message = payload.get("error_message") or "文案生成失败"
    image_description = payload.get("image_description")

    try:
        record = mark_failed(
            task_id=task_id,
            error_code=error_code,
            error_message=error_message,
            image_description=image_description,
        )
    except BusinessException as e:
        return jsonify(e.to_dict()), 404 if e.code == ErrorCode.TASK_NOT_FOUND else 500

    return success_response(record.to_dict(), "任务已标记失败")


@main_bp.route("/api/tasks/<task_id>", methods=["GET"])
def get_task(task_id: str):
    record = get_record(task_id)
    if not record:
        return error_response(ErrorCode.TASK_NOT_FOUND), 404
    return success_response(record.to_dict())


@main_bp.route("/api/tasks", methods=["GET"])
def get_tasks():
    status = request.args.get("status", default=None)
    limit = request.args.get("limit", default=100, type=int)
    records = list_records(status=status, limit=limit)
    return success_response([r.to_dict() for r in records])


@main_bp.errorhandler(BusinessException)
def handle_business_exception(e: BusinessException):
    return jsonify(e.to_dict()), 400 if e.code in (ErrorCode.PARAMS_ERROR, ErrorCode.VALIDATION_ERROR) else 500


@main_bp.errorhandler(Exception)
def handle_unexpected_exception(e: Exception):
    current_app.logger.exception("Unhandled exception: %s", e)
    return jsonify(error_response(ErrorCode.INTERNAL_ERROR, str(e))), 500
