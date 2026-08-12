"""Stable public API error responses."""

from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
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
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.headers = dict(headers) if headers is not None else None

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
        return JSONResponse(
            status_code=error.status_code,
            content=error.payload(),
            headers=error.headers,
        )

    @application.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        if _is_generation_form_field_too_large(request, error):
            api_error = APIError(
                code="FORM_FIELD_TOO_LARGE",
                message="表单文本字段过大，请缩短后重试。",
                status_code=400,
            )
            return JSONResponse(
                status_code=api_error.status_code,
                content=api_error.payload(),
            )
        return await http_exception_handler(request, error)

    @application.exception_handler(RequestValidationError)
    async def handle_request_validation(
        request: Request,
        error: RequestValidationError,
    ):
        if request.url.path == "/api/v1/auth/register":
            api_error = APIError(
                code="AUTH_INPUT_INVALID",
                message="注册信息无效，请检查后重试。",
                status_code=400,
            )
            return JSONResponse(
                status_code=api_error.status_code,
                content=api_error.payload(),
            )
        if request.url.path == "/api/v1/auth/login":
            api_error = APIError(
                code="INVALID_CREDENTIALS",
                message="邮箱或密码错误。",
                status_code=401,
            )
            return JSONResponse(
                status_code=api_error.status_code,
                content=api_error.payload(),
            )
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


def _is_generation_form_field_too_large(
    request: Request,
    error: StarletteHTTPException,
) -> bool:
    """Recognize only parser size failures on the generation endpoint."""
    parser_error = error.__context__
    if not isinstance(parser_error, MultiPartException):
        return False
    detail = parser_error.message
    return (
        request.method == "POST"
        and request.url.path == "/api/v1/generations"
        and error.status_code == 400
        and isinstance(detail, str)
        and detail.endswith("KB.")
        and detail.startswith(
            ("Part exceeded maximum size of ", "Field exceeded maximum size of ")
        )
    )
