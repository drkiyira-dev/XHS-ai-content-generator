"""Stable public API error responses."""

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from typing_extensions import TypedDict


class ErrorDetail(TypedDict):
    """Fields exposed for one API error."""

    code: str
    message: str
    retryable: bool


class ErrorResponse(TypedDict):
    """Top-level error envelope frozen for frontend consumers."""

    error: ErrorDetail


class APIError(Exception):
    """An expected failure safe to expose without a stack trace."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable

    def payload(self) -> ErrorResponse:
        """Build the frozen public error structure."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
            }
        }


def register_exception_handlers(application: FastAPI) -> None:
    """Install handlers that always preserve the frozen public error envelope."""

    @application.exception_handler(APIError)
    async def handle_api_error(_request: Request, error: APIError) -> JSONResponse:
        return JSONResponse(status_code=error.status_code, content=error.payload())

    @application.exception_handler(RequestValidationError)
    async def handle_request_validation(
        request: Request,
        error: RequestValidationError,
    ):
        missing_image = any(
            item.get("type") == "missing"
            and tuple(item.get("loc", ()))[-2:] == ("body", "image")
            for item in error.errors()
        )
        if missing_image:
            api_error = APIError(
                code="IMAGE_REQUIRED",
                message="请上传一张图片。",
                status_code=400,
            )
            return JSONResponse(
                status_code=api_error.status_code,
                content=api_error.payload(),
            )
        invalid_image = any(
            tuple(item.get("loc", ()))[-2:] == ("body", "image")
            for item in error.errors()
        )
        if invalid_image:
            api_error = APIError(
                code="UNSUPPORTED_IMAGE_TYPE",
                message="图片必须通过文件上传控件提交。",
                status_code=415,
            )
            return JSONResponse(
                status_code=api_error.status_code,
                content=api_error.payload(),
            )
        return await request_validation_exception_handler(request, error)

    @application.exception_handler(Exception)
    async def handle_unexpected_error(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        api_error = APIError(
            code="INTERNAL_ERROR",
            message="服务器内部错误，请稍后重试。",
            status_code=500,
        )
        return JSONResponse(
            status_code=api_error.status_code,
            content=api_error.payload(),
        )
