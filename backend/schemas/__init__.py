"""Schemas package：统一响应格式 + 错误码 + 业务异常（B 与 C 共用，零 Flask 依赖）。

导出：
    - ErrorCode：与 B 的 app.schemas 完全对齐
    - BusinessException：B/C 共用异常类
    - success_response / error_response：原 {code,message,data,success}
    - http_error_body / http_success_body：B 与 A 对齐的 HTTP 层格式
        { "error": { "code", "message", "retryable" } }
        { "data": ... , "success": true }
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class ErrorCode:
    SUCCESS = "SUCCESS"
    PARAMS_ERROR = "PARAMS_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    IMAGE_PROCESS_ERROR = "IMAGE_PROCESS_ERROR"
    LLM_GENERATE_ERROR = "LLM_GENERATE_ERROR"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    INTERNAL_ERROR = "INTERNAL_ERROR"


ERROR_MESSAGES = {
    ErrorCode.SUCCESS: "操作成功",
    ErrorCode.PARAMS_ERROR: "请求参数错误",
    ErrorCode.VALIDATION_ERROR: "数据校验失败",
    ErrorCode.DATABASE_ERROR: "数据库操作失败",
    ErrorCode.IMAGE_PROCESS_ERROR: "图片处理失败",
    ErrorCode.LLM_GENERATE_ERROR: "文案生成失败",
    ErrorCode.TASK_NOT_FOUND: "任务不存在",
    ErrorCode.INTERNAL_ERROR: "服务器内部错误",
}


# 错误码是否可重试（用于 HTTP error.retryable 字段）
_RETRYABLE = {
    ErrorCode.DATABASE_ERROR: True,
    ErrorCode.LLM_GENERATE_ERROR: True,
    ErrorCode.IMAGE_PROCESS_ERROR: False,
    ErrorCode.PARAMS_ERROR: False,
    ErrorCode.VALIDATION_ERROR: False,
    ErrorCode.TASK_NOT_FOUND: False,
    ErrorCode.INTERNAL_ERROR: True,
}


def is_retryable(code: str) -> bool:
    return _RETRYABLE.get(code, True)


# --------------------------------------------------------------------------
# 原始响应格式（与旧 app/schemas 对齐，保证兼容）
# --------------------------------------------------------------------------

def success_response(data: Any = None, message: str = "success") -> Dict[str, Any]:
    return {
        "code": ErrorCode.SUCCESS,
        "message": message,
        "data": data,
        "success": True,
    }


def error_response(
    code: str,
    message: Optional[str] = None,
    data: Any = None,
) -> Dict[str, Any]:
    return {
        "code": code,
        "message": message or ERROR_MESSAGES.get(code, "未知错误"),
        "data": data,
        "success": False,
    }


# --------------------------------------------------------------------------
# B 与 A 对齐的 HTTP 层格式
#   成功: { "data": ..., "success": true }
#   失败: { "error": { "code", "message", "retryable" } }
# --------------------------------------------------------------------------

def http_success_body(data: Any = None, **extra) -> Dict[str, Any]:
    body: Dict[str, Any] = {"success": True, "data": data}
    body.update(extra)
    return body


def http_error_body(
    code: str,
    message: Optional[str] = None,
    retryable: Optional[bool] = None,
    details: Any = None,
) -> Dict[str, Any]:
    if retryable is None:
        retryable = is_retryable(code)
    err: Dict[str, Any] = {
        "code": code,
        "message": message or ERROR_MESSAGES.get(code, "未知错误"),
        "retryable": retryable,
    }
    if details is not None:
        err["details"] = details
    return {"error": err}


class BusinessException(Exception):
    """业务异常。接口层两种用法：
    1. e.to_dict()        -> 旧格式：{code,message,data,success}
    2. e.to_http_error()  -> HTTP 层格式：{error:{code,message,retryable}}
    """

    def __init__(
        self,
        code: str,
        message: Optional[str] = None,
        data: Any = None,
        retryable: Optional[bool] = None,
    ):
        self.code = code
        self.message = message or ERROR_MESSAGES.get(code, "未知错误")
        self.data = data
        self.retryable = retryable if retryable is not None else is_retryable(code)
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        return error_response(self.code, self.message, self.data)

    def to_http_error(self) -> Dict[str, Any]:
        return http_error_body(
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            details=self.data,
        )
