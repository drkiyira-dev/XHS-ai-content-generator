"""HTTP security and response contracts for local account authentication."""

import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from anyio import CapacityLimiter
from fastapi import FastAPI
import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app import create_app
from backend.core.config import Settings
from backend.db import AuthSession, Base
from backend.services.auth import (
    AuthenticatedUser,
    AuthService,
    AuthUnavailable,
    EmailUnavailable,
    InvalidAuthInput,
    InvalidCredentials,
    IssuedSession,
    RateLimitExceeded,
    SQLAlchemyAuthStore,
)
from backend.services.persistence import NoOpGenerationPersistence
from tests.support import StubModelService


CSRF_HEADERS = {
    "Origin": "http://127.0.0.1:5173",
    "X-XHS-CSRF": "1",
}
RAW_TOKEN = "A" * 43
PRIVATE_PASSWORD = "private-password-value"


class FakeAuthService:
    """Controllable service double that records only operation boundaries."""

    def __init__(self) -> None:
        self.user = AuthenticatedUser(7, "demo@example.com", False)
        self.issued = IssuedSession(
            self.user,
            RAW_TOKEN,
            datetime.now(UTC) + timedelta(days=7),
        )
        self.calls: list[tuple[str, object]] = []
        self.register_error: Exception | None = None
        self.login_error: Exception | None = None
        self.current_error: Exception | None = None
        self.logout_error: Exception | None = None
        self.current_user: AuthenticatedUser | None = self.user

    async def register(self, *, email: object, password: object) -> IssuedSession:
        self.calls.append(("register", email))
        assert password == PRIVATE_PASSWORD
        if self.register_error is not None:
            raise self.register_error
        return self.issued

    async def login(self, *, email: object, password: object) -> IssuedSession:
        self.calls.append(("login", email))
        assert password == PRIVATE_PASSWORD
        if self.login_error is not None:
            raise self.login_error
        return self.issued

    async def get_current_user(
        self,
        raw_token: object,
    ) -> AuthenticatedUser | None:
        self.calls.append(("get_current_user", raw_token))
        if self.current_error is not None:
            raise self.current_error
        return self.current_user

    async def logout(self, raw_token: object) -> None:
        self.calls.append(("logout", raw_token))
        if self.logout_error is not None:
            raise self.logout_error


class FakeRateLimiter:
    """Limiter double used to prove it runs before password work."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.registration_error: Exception | None = None
        self.login_error: Exception | None = None
        self.success_error: Exception | None = None

    async def check_registration(self, *, client_ip: object) -> None:
        self.calls.append(("check_registration", client_ip))
        if self.registration_error is not None:
            raise self.registration_error

    async def check_login(self, *, email: object, client_ip: object) -> None:
        self.calls.append(("check_login", (email, client_ip)))
        if self.login_error is not None:
            raise self.login_error

    async def record_login_success(self, *, email: object) -> None:
        self.calls.append(("record_login_success", email))
        if self.success_error is not None:
            raise self.success_error


def _settings(*, secure_cookie: bool = False) -> Settings:
    origin = "https://app.example" if secure_cookie else "http://127.0.0.1:5173"
    return Settings(
        SILICONFLOW_API_KEY="test-secret-key",
        SILICONFLOW_BASE_URL="https://api.siliconflow.cn/v1",
        VISION_MODEL_NAME="qwen-test-model",
        OCR_MODEL_NAME="paddle-test-model",
        CORS_ALLOW_ORIGINS=origin,
        DATABASE_ENABLED=True,
        DATABASE_URL="mysql+pymysql://test:test@127.0.0.1/test",
        AUTH_ENABLED=True,
        AUTH_COOKIE_SECURE=secure_cookie,
        _env_file=None,
    )


def _application(
    service: FakeAuthService | None = None,
    limiter: FakeRateLimiter | None = None,
    *,
    secure_cookie: bool = False,
) -> FastAPI:
    return create_app(
        _settings(secure_cookie=secure_cookie),
        model_service=StubModelService(),
        generation_persistence=NoOpGenerationPersistence(),
        auth_service=service or FakeAuthService(),
        auth_rate_limiter=limiter or FakeRateLimiter(),  # type: ignore[arg-type]
    )


def _credentials() -> dict[str, str]:
    return {
        "email": " Demo@Example.COM ",
        "password": PRIVATE_PASSWORD,
    }


def _assert_no_store(response: httpx.Response) -> None:
    assert response.headers["cache-control"] == "no-store"


def test_register_sets_only_a_scoped_httponly_cookie_and_safe_body() -> None:
    service = FakeAuthService()
    limiter = FakeRateLimiter()
    application = _application(service, limiter)

    async def scenario() -> httpx.Response:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
        ) as client:
            return await client.post(
                "/api/v1/auth/register",
                json=_credentials(),
                headers=CSRF_HEADERS,
            )

    response = asyncio.run(scenario())

    assert response.status_code == 201
    assert response.json() == {
        "user": {
            "user_id": 7,
            "email": "demo@example.com",
            "email_verified": False,
        }
    }
    assert RAW_TOKEN not in response.text
    assert PRIVATE_PASSWORD not in response.text
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"xhs_session_local={RAW_TOKEN};")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/api/v1" in cookie
    assert "Max-Age=604800" in cookie
    assert "Domain=" not in cookie
    assert "Secure" not in cookie
    assert limiter.calls == [("check_registration", "127.0.0.1")]
    assert service.calls == [("register", " Demo@Example.COM ")]
    _assert_no_store(response)


def test_login_me_and_logout_use_cookie_identity_and_revoke_it() -> None:
    service = FakeAuthService()
    limiter = FakeRateLimiter()
    application = _application(service, limiter)

    async def scenario() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
        ) as client:
            login = await client.post(
                "/api/v1/auth/login",
                json=_credentials(),
                headers=CSRF_HEADERS,
            )
            me = await client.get("/api/v1/auth/me")
            logout = await client.post(
                "/api/v1/auth/logout",
                headers=CSRF_HEADERS,
            )
            return login, me, logout

    login, me, logout = asyncio.run(scenario())

    assert login.status_code == 200
    assert me.status_code == 200
    assert logout.status_code == 204
    assert logout.content == b""
    assert limiter.calls == [
        ("check_login", (" Demo@Example.COM ", "127.0.0.1")),
        ("record_login_success", "demo@example.com"),
    ]
    assert service.calls == [
        ("login", " Demo@Example.COM "),
        ("get_current_user", RAW_TOKEN),
        ("logout", RAW_TOKEN),
    ]
    deletion = logout.headers["set-cookie"]
    assert deletion.startswith(('xhs_session_local=;', 'xhs_session_local="";'))
    assert "Max-Age=0" in deletion
    assert "Path=/api/v1" in deletion
    assert "HttpOnly" in deletion
    for response in (login, me, logout):
        _assert_no_store(response)


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://127.0.0.1:5173"},
        {"Origin": "http://evil.example", "X-XHS-CSRF": "1"},
        {
            "Origin": "http://127.0.0.1:5173",
            "X-XHS-CSRF": "1",
            "Sec-Fetch-Site": "cross-site",
        },
    ],
)
def test_state_changing_routes_reject_cross_site_requests_before_work(
    headers: dict[str, str],
) -> None:
    service = FakeAuthService()
    limiter = FakeRateLimiter()
    application = _application(service, limiter)

    async def scenario() -> httpx.Response:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
        ) as client:
            return await client.post(
                "/api/v1/auth/register",
                json=_credentials(),
                headers=headers,
            )

    response = asyncio.run(scenario())

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_REJECTED"
    assert service.calls == []
    assert limiter.calls == []
    _assert_no_store(response)


def test_non_browser_client_may_omit_origin_but_not_csrf_marker() -> None:
    application = _application()

    async def scenario() -> httpx.Response:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
        ) as client:
            return await client.post(
                "/api/v1/auth/register",
                json=_credentials(),
                headers={"X-XHS-CSRF": "1"},
            )

    assert asyncio.run(scenario()).status_code == 201


@pytest.mark.parametrize(
    ("path", "include_body"),
    [
        ("/api/v1/auth/register", True),
        ("/api/v1/auth/login", True),
        ("/api/v1/auth/logout", False),
    ],
)
def test_every_auth_write_requires_csrf_before_service_work(
    path: str,
    include_body: bool,
) -> None:
    service = FakeAuthService()
    limiter = FakeRateLimiter()
    application = _application(service, limiter)

    async def scenario() -> httpx.Response:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
        ) as client:
            kwargs: dict[str, object] = {}
            if include_body:
                kwargs["json"] = _credentials()
            return await client.post(path, **kwargs)

    response = asyncio.run(scenario())

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_REJECTED"
    assert service.calls == []
    assert limiter.calls == []
    _assert_no_store(response)


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (InvalidAuthInput(), 400, "AUTH_INPUT_INVALID"),
        (EmailUnavailable(), 409, "EMAIL_UNAVAILABLE"),
        (AuthUnavailable(), 503, "AUTH_UNAVAILABLE"),
        (RuntimeError("private provider detail"), 503, "AUTH_UNAVAILABLE"),
    ],
)
def test_register_errors_are_fixed_and_provider_details_are_redacted(
    error: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    service = FakeAuthService()
    service.register_error = error
    application = _application(service)

    async def scenario() -> httpx.Response:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
        ) as client:
            return await client.post(
                "/api/v1/auth/register",
                json=_credentials(),
                headers=CSRF_HEADERS,
            )

    response = asyncio.run(scenario())

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code
    assert "private provider detail" not in response.text
    assert PRIVATE_PASSWORD not in response.text
    _assert_no_store(response)


@pytest.mark.parametrize("error", [InvalidAuthInput(), InvalidCredentials()])
def test_login_failures_share_one_non_enumerating_error(error: Exception) -> None:
    service = FakeAuthService()
    service.login_error = error
    application = _application(service)

    async def scenario() -> httpx.Response:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
        ) as client:
            return await client.post(
                "/api/v1/auth/login",
                json=_credentials(),
                headers=CSRF_HEADERS,
            )

    response = asyncio.run(scenario())

    assert response.status_code == 401
    assert response.json()["error"] == {
        "code": "INVALID_CREDENTIALS",
        "message": "邮箱或密码错误。",
        "retryable": False,
    }
    _assert_no_store(response)


def test_rate_limit_is_enforced_before_service_and_retry_is_coarsened() -> None:
    service = FakeAuthService()
    limiter = FakeRateLimiter()
    limiter.registration_error = RateLimitExceeded(61)
    application = _application(service, limiter)

    async def scenario() -> httpx.Response:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
        ) as client:
            return await client.post(
                "/api/v1/auth/register",
                json=_credentials(),
                headers=CSRF_HEADERS,
            )

    response = asyncio.run(scenario())

    assert response.status_code == 429
    assert response.headers["retry-after"] == "120"
    assert response.json()["error"] == {
        "code": "RATE_LIMITED",
        "message": "请求过于频繁，请稍后重试。",
        "retryable": True,
    }
    assert service.calls == []
    _assert_no_store(response)


def test_login_still_returns_issued_cookie_if_success_bucket_cleanup_fails() -> None:
    service = FakeAuthService()
    limiter = FakeRateLimiter()
    limiter.success_error = RuntimeError("private limiter detail")
    application = _application(service, limiter)

    async def scenario() -> httpx.Response:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
        ) as client:
            return await client.post(
                "/api/v1/auth/login",
                json=_credentials(),
                headers=CSRF_HEADERS,
            )

    response = asyncio.run(scenario())

    assert response.status_code == 200
    assert response.headers["set-cookie"].startswith(
        f"xhs_session_local={RAW_TOKEN};"
    )
    assert service.calls == [("login", " Demo@Example.COM ")]
    assert limiter.calls == [
        ("check_login", (" Demo@Example.COM ", "127.0.0.1")),
        ("record_login_success", "demo@example.com"),
    ]
    assert "private limiter detail" not in response.text
    _assert_no_store(response)


def test_validation_errors_never_echo_password_or_raw_validation_details() -> None:
    application = _application()
    oversized_password = "sensitive-" + "x" * 503

    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
        ) as client:
            register = await client.post(
                "/api/v1/auth/register",
                json={"email": "demo@example.com", "password": oversized_password},
                headers=CSRF_HEADERS,
            )
            login = await client.post(
                "/api/v1/auth/login",
                json={"email": "demo@example.com"},
                headers=CSRF_HEADERS,
            )
            return register, login

    register, login = asyncio.run(scenario())

    assert register.status_code == 400
    assert register.json()["error"]["code"] == "AUTH_INPUT_INVALID"
    assert login.status_code == 401
    assert login.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert oversized_password not in register.text
    assert "password" not in register.text.casefold()
    assert "password" not in login.text.casefold()
    _assert_no_store(register)
    _assert_no_store(login)


def test_invalid_or_unavailable_session_uses_safe_fixed_errors() -> None:
    service = FakeAuthService()
    service.current_user = None
    application = _application(service)

    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
        ) as client:
            client.cookies.set(
                "xhs_session_local",
                RAW_TOKEN,
                domain="127.0.0.1",
                path="/api/v1",
            )
            invalid = await client.get("/api/v1/auth/me")
            service.current_error = RuntimeError("database-private-detail")
            unavailable = await client.get("/api/v1/auth/me")
            return invalid, unavailable

    invalid, unavailable = asyncio.run(scenario())

    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "AUTH_REQUIRED"
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "AUTH_UNAVAILABLE"
    assert RAW_TOKEN not in invalid.text + unavailable.text
    assert "database-private-detail" not in unavailable.text
    _assert_no_store(invalid)
    _assert_no_store(unavailable)


def test_logout_failure_preserves_cookie_for_a_revocation_retry() -> None:
    service = FakeAuthService()
    service.logout_error = RuntimeError("database-private-detail")
    application = _application(service)

    async def scenario() -> httpx.Response:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
        ) as client:
            client.cookies.set(
                "xhs_session_local",
                RAW_TOKEN,
                domain="127.0.0.1",
                path="/api/v1",
            )
            return await client.post(
                "/api/v1/auth/logout",
                headers=CSRF_HEADERS,
            )

    response = asyncio.run(scenario())

    assert response.status_code == 503
    assert "set-cookie" not in response.headers
    assert RAW_TOKEN not in response.text
    _assert_no_store(response)


def test_secure_mode_uses_a_host_only_secure_cookie() -> None:
    application = _application(secure_cookie=True)

    async def scenario() -> httpx.Response:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://app.example",
        ) as client:
            return await client.post(
                "/api/v1/auth/register",
                json=_credentials(),
                headers={"Origin": "https://app.example", "X-XHS-CSRF": "1"},
            )

    response = asyncio.run(scenario())
    cookie = response.headers["set-cookie"]

    assert response.status_code == 201
    assert cookie.startswith(f"__Host-xhs_session={RAW_TOKEN};")
    assert "Path=/" in cookie
    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "Domain=" not in cookie


def test_real_sqlite_service_register_me_logout_cookie_lifecycle(
    tmp_path: Path,
) -> None:
    """Prove the HTTP cookie drives a real persisted and revoked session."""
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'auth-api.sqlite3'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )
    persisted_token = "R" * 43
    now = datetime.now(UTC)
    service = AuthService(
        SQLAlchemyAuthStore(factory, limiter=CapacityLimiter(4)),
        password_limiter=CapacityLimiter(2),
        clock=lambda: now,
        token_factory=lambda: persisted_token,
    )
    application = _application(service=service)  # type: ignore[arg-type]

    async def scenario() -> tuple[
        httpx.Response,
        httpx.Response,
        httpx.Response,
        httpx.Response,
    ]:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
        ) as client:
            register = await client.post(
                "/api/v1/auth/register",
                json=_credentials(),
                headers=CSRF_HEADERS,
            )
            me = await client.get("/api/v1/auth/me")
            logout = await client.post(
                "/api/v1/auth/logout",
                headers=CSRF_HEADERS,
            )
            revoked = await client.get(
                "/api/v1/auth/me",
                headers={"Cookie": f"xhs_session_local={persisted_token}"},
            )
            return register, me, logout, revoked

    try:
        register, me, logout, revoked = asyncio.run(scenario())
        with factory() as database_session:
            stored_session = database_session.execute(
                select(AuthSession)
            ).scalar_one()
    finally:
        engine.dispose()

    assert register.status_code == 201
    assert me.status_code == 200
    assert logout.status_code == 204
    assert revoked.status_code == 401
    assert register.json()["user"]["email"] == "demo@example.com"
    assert stored_session.token_hash == sha256(persisted_token.encode("ascii")).digest()
    assert stored_session.revoked_at is not None
    assert persisted_token not in register.text + me.text + revoked.text


def test_openapi_marks_password_write_only_and_documents_csrf_header() -> None:
    schema: dict[str, Any] = _application().openapi()
    password = schema["components"]["schemas"]["AuthRequest"]["properties"][
        "password"
    ]
    register_parameters = schema["paths"]["/api/v1/auth/register"]["post"][
        "parameters"
    ]
    register_responses = schema["paths"]["/api/v1/auth/register"]["post"][
        "responses"
    ]
    login_responses = schema["paths"]["/api/v1/auth/login"]["post"][
        "responses"
    ]

    assert password["format"] == "password"
    assert password["writeOnly"] is True
    assert password["maxLength"] == 512
    assert any(
        parameter["in"] == "header" and parameter["name"] == "X-XHS-CSRF"
        for parameter in register_parameters
    )
    assert "422" not in register_responses
    assert "422" not in login_responses


def test_auth_routes_are_absent_when_authentication_is_disabled() -> None:
    settings = Settings(
        SILICONFLOW_API_KEY="test-secret-key",
        SILICONFLOW_BASE_URL="https://api.siliconflow.cn/v1",
        VISION_MODEL_NAME="qwen-test-model",
        OCR_MODEL_NAME="paddle-test-model",
        DATABASE_ENABLED=False,
        AUTH_ENABLED=False,
        _env_file=None,
    )
    application = create_app(
        settings,
        model_service=StubModelService(),
        generation_persistence=NoOpGenerationPersistence(),
    )

    assert not any(
        getattr(route, "path", "").startswith("/api/v1/auth")
        for route in application.routes
    )
