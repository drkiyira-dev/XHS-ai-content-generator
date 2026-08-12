"""Authentication dependencies and request-boundary security checks."""

from typing import Annotated, cast
from urllib.parse import urlsplit

from fastapi import Depends, Header, Request, Response

from backend.api.errors import APIError
from backend.core.config import Settings
from backend.services.auth import (
    AuthenticatedUser,
    AuthenticationService,
    AuthRateLimiter,
)


CSRF_HEADER_NAME = "X-XHS-CSRF"
CSRF_HEADER_VALUE = "1"
AUTH_CACHE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
}
CsrfHeader = Annotated[
    str | None,
    Header(alias=CSRF_HEADER_NAME, description="Required value: 1"),
]


def get_auth_service(request: Request) -> AuthenticationService:
    """Return the application-owned singleton authentication service."""
    service = getattr(request.app.state, "auth_service", None)
    if service is None:
        raise auth_unavailable_error()
    return cast(AuthenticationService, service)


def get_auth_rate_limiter(request: Request) -> AuthRateLimiter:
    """Return the application-owned in-memory limiter singleton."""
    limiter = getattr(request.app.state, "auth_rate_limiter", None)
    if limiter is None:
        raise auth_unavailable_error()
    return cast(AuthRateLimiter, limiter)


def require_csrf_request(
    request: Request,
    marker: CsrfHeader = None,
) -> None:
    """Require a preflight-forcing marker and reject cross-site browsers."""
    if marker != CSRF_HEADER_VALUE:
        raise csrf_rejected_error()
    if request.headers.get("sec-fetch-site", "").casefold() == "cross-site":
        raise csrf_rejected_error()

    origin = request.headers.get("origin")
    if origin is None:
        # Non-browser clients may omit Origin, but still need the custom marker.
        return

    settings: Settings = request.app.state.settings
    allowed_origins = set(settings.cors_origins)
    request_host = request.url.hostname
    if request_host in {"127.0.0.1", "localhost", "testserver"}:
        # Permit the same local API origin so Swagger remains usable.
        allowed_origins.add(_request_origin(request))
    if origin not in allowed_origins:
        raise csrf_rejected_error()


async def require_current_user(
    request: Request,
    service: Annotated[AuthenticationService, Depends(get_auth_service)],
) -> AuthenticatedUser:
    """Resolve identity exclusively from the configured HttpOnly cookie."""
    settings: Settings = request.app.state.settings
    raw_token = request.cookies.get(settings.auth_cookie_name)
    if raw_token is None:
        raise auth_required_error()

    user: AuthenticatedUser | None = None
    unavailable = False
    try:
        user = await service.get_current_user(raw_token)
    except Exception:
        # No provider exception, traceback, or token is allowed past this boundary.
        unavailable = True
    finally:
        raw_token = None
    if unavailable:
        raise auth_unavailable_error() from None
    if user is None:
        raise auth_required_error()
    return user


AuthServiceDependency = Annotated[
    AuthenticationService,
    Depends(get_auth_service),
]
AuthRateLimiterDependency = Annotated[
    AuthRateLimiter,
    Depends(get_auth_rate_limiter),
]
CsrfGuard = Annotated[None, Depends(require_csrf_request)]
CurrentUser = Annotated[AuthenticatedUser, Depends(require_current_user)]


def mark_auth_response_no_store(response: Response) -> None:
    """Prevent browser or intermediary caching of account responses."""
    for name, value in AUTH_CACHE_HEADERS.items():
        response.headers[name] = value


def _request_origin(request: Request) -> str:
    """Build the local API origin without accepting paths or credentials."""
    parsed = urlsplit(str(request.base_url))
    return f"{parsed.scheme}://{parsed.netloc}"


def csrf_rejected_error() -> APIError:
    return APIError(
        code="CSRF_REJECTED",
        message="请求来源校验失败。",
        status_code=403,
        headers=AUTH_CACHE_HEADERS,
    )


def auth_required_error() -> APIError:
    return APIError(
        code="AUTH_REQUIRED",
        message="请先登录。",
        status_code=401,
        headers=AUTH_CACHE_HEADERS,
    )


def auth_unavailable_error() -> APIError:
    return APIError(
        code="AUTH_UNAVAILABLE",
        message="账号服务暂时不可用，请稍后重试。",
        status_code=503,
        retryable=True,
        headers=AUTH_CACHE_HEADERS,
    )


__all__ = [
    "AUTH_CACHE_HEADERS",
    "CSRF_HEADER_NAME",
    "CSRF_HEADER_VALUE",
    "AuthRateLimiterDependency",
    "AuthServiceDependency",
    "CsrfGuard",
    "CurrentUser",
    "auth_required_error",
    "auth_unavailable_error",
    "csrf_rejected_error",
    "get_auth_rate_limiter",
    "get_auth_service",
    "mark_auth_response_no_store",
    "require_csrf_request",
    "require_current_user",
]
