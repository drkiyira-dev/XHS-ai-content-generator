"""Schemas package: 统一响应结构 + 错误码 + 业务异常。

成员 B 直接 import ErrorCode / BusinessException / success_response / error_response 即可。
"""
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
    ErrorCode.SUCCESS: "成功",
    ErrorCode.PARAMS_ERROR: "请求参数错误",
    ErrorCode.VALIDATION_ERROR: "数据校验失败",
    ErrorCode.DATABASE_ERROR: "数据库操作失败",
    ErrorCode.IMAGE_PROCESS_ERROR: "图片处理失败",
    ErrorCode.LLM_GENERATE_ERROR: "文案生成失败",
    ErrorCode.TASK_NOT_FOUND: "任务不存在",
    ErrorCode.INTERNAL_ERROR: "服务器内部错误",
}


def success_response(data: Any = None, message: str = "success") -> Dict[str, Any]:
    return {
        "code": ErrorCode.SUCCESS,
        "message": message,
        "data": data,
        "success": True,
    }


def error_response(code: str, message: Optional[str] = None, data: Any = None) -> Dict[str, Any]:
    return {
        "code": code,
        "message": message or ERROR_MESSAGES.get(code, "未知错误"),
        "data": data,
        "success": False,
    }


class BusinessException(Exception):
    """业务异常。接口层捕获后可直接用 .to_dict() 返回给前端。"""

    def __init__(self, code: str, message: Optional[str] = None, data: Any = None):
        self.code = code
        self.message = message or ERROR_MESSAGES.get(code, "未知错误")
        self.data = data
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        return error_response(self.code, self.message, self.data)
