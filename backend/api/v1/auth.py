"""Local demonstration account HTTP endpoints."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from backend.api.auth_dependencies import (
    AUTH_CACHE_HEADERS,
    AuthRateLimiterDependency,
    AuthServiceDependency,
    CsrfGuard,
    CurrentUser,
    auth_unavailable_error,
    mark_auth_response_no_store,
)
from backend.api.errors import APIError, ErrorResponse
from backend.core.config import Settings
from backend.services.auth import (
    AuthenticatedUser,
    AuthRateLimiter,
    AuthUnavailable,
    EmailUnavailable,
    InvalidAuthInput,
    InvalidCredentials,
    IssuedSession,
    RateLimitExceeded,
)


SESSION_COOKIE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


class AuthRequest(BaseModel):
    """Credentials accepted only at the registration and login boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)

    email: Annotated[str, Field(min_length=1, max_length=1_024)]
    password: Annotated[
        SecretStr,
        Field(
            max_length=512,
            json_schema_extra={"format": "password", "writeOnly": True},
        ),
    ]


class UserResponse(BaseModel):
    """Account fields safe to expose to the local frontend."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: Annotated[int, Field(gt=0)]
    email: str
    email_verified: bool


class AuthResponse(BaseModel):
    """Shared registration, login, and session-check response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user: UserResponse


router = APIRouter(prefix="/auth", tags=["authentication"])


AUTH_ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "Invalid account input"},
    401: {"model": ErrorResponse, "description": "Authentication failed"},
    403: {"model": ErrorResponse, "description": "Request origin rejected"},
    409: {"model": ErrorResponse, "description": "Email unavailable"},
    429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    503: {"model": ErrorResponse, "description": "Account service unavailable"},
}


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    responses=AUTH_ERROR_RESPONSES,
    summary="Register a local demonstration account",
)
async def register_account(
    payload: AuthRequest,
    request: Request,
    response: Response,
    _csrf: CsrfGuard,
    service: AuthServiceDependency,
    limiter: AuthRateLimiterDependency,
) -> AuthResponse:
    """Create one free account and return its session only as a cookie."""
    email = payload.email
    password = payload.password.get_secret_value()
    del payload
    issued: IssuedSession | None = None
    error: APIError | None = None
    try:
        await _check_registration_limit(limiter, request)
        try:
            issued = await service.register(email=email, password=password)
        except InvalidAuthInput:
            error = _invalid_registration_input()
        except EmailUnavailable:
            error = _email_unavailable()
        except AuthUnavailable:
            error = auth_unavailable_error()
        except Exception:
            error = auth_unavailable_error()
    finally:
        password = ""
        del email

    if error is not None:
        raise error
    if issued is None:
        raise auth_unavailable_error()

    _set_session_cookie(response, request.app.state.settings, issued)
    mark_auth_response_no_store(response)
    return _auth_response(issued.user)


@router.post(
    "/login",
    response_model=AuthResponse,
    responses=AUTH_ERROR_RESPONSES,
    summary="Log in to a local demonstration account",
)
async def login_account(
    payload: AuthRequest,
    request: Request,
    response: Response,
    _csrf: CsrfGuard,
    service: AuthServiceDependency,
    limiter: AuthRateLimiterDependency,
) -> AuthResponse:
    """Verify credentials and return a fresh session only as a cookie."""
    email = payload.email
    password = payload.password.get_secret_value()
    del payload
    issued: IssuedSession | None = None
    error: APIError | None = None
    try:
        await _check_login_limit(limiter, request, email)
        try:
            issued = await service.login(email=email, password=password)
        except (InvalidAuthInput, InvalidCredentials):
            error = _invalid_credentials()
        except AuthUnavailable:
            error = auth_unavailable_error()
        except Exception:
            error = auth_unavailable_error()
    finally:
        password = ""
        del email

    if error is not None:
        raise error
    if issued is None:
        raise auth_unavailable_error()

    await _record_login_success(limiter, issued.user.email)
    _set_session_cookie(response, request.app.state.settings, issued)
    mark_auth_response_no_store(response)
    return _auth_response(issued.user)


@router.get(
    "/me",
    response_model=AuthResponse,
    responses={
        401: AUTH_ERROR_RESPONSES[401],
        503: AUTH_ERROR_RESPONSES[503],
    },
    summary="Get the current local account",
)
async def get_current_account(
    response: Response,
    user: CurrentUser,
) -> AuthResponse:
    """Return the account resolved solely from the HttpOnly cookie."""
    mark_auth_response_no_store(response)
    return _auth_response(user)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        403: AUTH_ERROR_RESPONSES[403],
        503: AUTH_ERROR_RESPONSES[503],
    },
    summary="Log out the current local session",
)
async def logout_account(
    request: Request,
    _csrf: CsrfGuard,
    service: AuthServiceDependency,
) -> Response:
    """Idempotently revoke the current token and expire its browser cookie."""
    settings: Settings = request.app.state.settings
    raw_token = request.cookies.get(settings.auth_cookie_name)
    error: APIError | None = None
    if raw_token is not None:
        try:
            await service.logout(raw_token)
        except Exception:
            error = auth_unavailable_error()
        finally:
            raw_token = None

    if error is not None:
        # Preserve the cookie so the browser can retry server-side revocation.
        raise error

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        settings.auth_cookie_name,
        path=settings.auth_cookie_path,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    mark_auth_response_no_store(response)
    return response


async def _check_registration_limit(
    limiter: AuthRateLimiter,
    request: Request,
) -> None:
    error: APIError | None = None
    try:
        await limiter.check_registration(client_ip=_client_ip(request))
    except RateLimitExceeded as exceeded:
        error = _rate_limited(exceeded.retry_after)
    except Exception:
        error = auth_unavailable_error()
    if error is not None:
        raise error


async def _check_login_limit(
    limiter: AuthRateLimiter,
    request: Request,
    email: object,
) -> None:
    error: APIError | None = None
    try:
        await limiter.check_login(email=email, client_ip=_client_ip(request))
    except RateLimitExceeded as exceeded:
        error = _rate_limited(exceeded.retry_after)
    except Exception:
        error = auth_unavailable_error()
    finally:
        email = None
    if error is not None:
        raise error


async def _record_login_success(
    limiter: AuthRateLimiter,
    email: object,
) -> None:
    """Best-effort cleanup after the enforcing pre-login check succeeded."""
    try:
        await limiter.record_login_success(email=email)
    except Exception:
        # Failing to clear a bucket is restrictive, not an authentication bypass.
        # Do not turn an already-created database session into an orphan token.
        return


def _client_ip(request: Request) -> object:
    """Use the direct peer only; never trust caller-controlled proxy headers."""
    return None if request.client is None else request.client.host


def _set_session_cookie(
    response: Response,
    settings: Settings,
    issued: IssuedSession,
) -> None:
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=issued.token,
        max_age=SESSION_COOKIE_MAX_AGE_SECONDS,
        expires=issued.expires_at,
        path=settings.auth_cookie_path,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _auth_response(user: AuthenticatedUser) -> AuthResponse:
    return AuthResponse(
        user=UserResponse(
            user_id=user.user_id,
            email=user.email,
            email_verified=user.email_verified,
        )
    )


def _invalid_registration_input() -> APIError:
    return APIError(
        code="AUTH_INPUT_INVALID",
        message="注册信息无效，请检查后重试。",
        status_code=400,
        headers=AUTH_CACHE_HEADERS,
    )


def _email_unavailable() -> APIError:
    return APIError(
        code="EMAIL_UNAVAILABLE",
        message="该邮箱暂不可用于注册。",
        status_code=409,
        headers=AUTH_CACHE_HEADERS,
    )


def _invalid_credentials() -> APIError:
    return APIError(
        code="INVALID_CREDENTIALS",
        message="邮箱或密码错误。",
        status_code=401,
        headers=AUTH_CACHE_HEADERS,
    )


def _rate_limited(retry_after: int) -> APIError:
    coarse_retry_after = min(3_600, max(60, ((int(retry_after) + 59) // 60) * 60))
    headers = dict(AUTH_CACHE_HEADERS)
    headers["Retry-After"] = str(coarse_retry_after)
    return APIError(
        code="RATE_LIMITED",
        message="请求过于频繁，请稍后重试。",
        status_code=429,
        retryable=True,
        headers=headers,
    )


__all__ = [
    "AuthRequest",
    "AuthResponse",
    "UserResponse",
    "router",
]
