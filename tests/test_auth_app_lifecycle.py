"""Application assembly contracts for explicitly enabled authentication."""

import asyncio
import sys
from types import ModuleType
from typing import Any

from fastapi import APIRouter, Response
import pytest

import backend.app as app_module
from backend.app import create_app
from backend.core.config import Settings
from backend.services.auth import AuthService, AuthenticatedUser, IssuedSession
from backend.services.auth.rate_limit import AuthRateLimiter
from backend.services.persistence import NoOpGenerationPersistence
from backend.services.persistence.runtime import DatabaseStartupError
from tests.support import StubModelService, send_request


DATABASE_URL = (
    "mysql+pymysql://xhs_app:test-only-password@"
    "127.0.0.1:3306/xhs_ai_test"
)


class AuthenticationServiceDouble:
    async def register(self, *, email: object, password: object) -> IssuedSession:
        raise AssertionError((email, password))

    async def login(self, *, email: object, password: object) -> IssuedSession:
        raise AssertionError((email, password))

    async def get_current_user(
        self,
        raw_token: object,
    ) -> AuthenticatedUser | None:
        _ = raw_token
        return None

    async def logout(self, raw_token: object) -> None:
        _ = raw_token


class RuntimeDouble:
    def __init__(self, *, auth_store: object | None = None) -> None:
        self.persistence = NoOpGenerationPersistence()
        self.auth_store = auth_store
        self.startup_calls = 0
        self.close_calls = 0

    async def startup(self) -> None:
        self.startup_calls += 1

    async def aclose(self) -> None:
        self.close_calls += 1


def make_settings(*, auth_enabled: bool) -> Settings:
    return Settings(
        SILICONFLOW_API_KEY="test-secret-key",
        SILICONFLOW_BASE_URL="https://api.siliconflow.cn/v1",
        VISION_MODEL_NAME="qwen-test-model",
        OCR_MODEL_NAME="paddle-test-model",
        CORS_ALLOW_ORIGINS="http://localhost:5173",
        DATABASE_ENABLED=auth_enabled,
        DATABASE_URL=DATABASE_URL if auth_enabled else None,
        AUTH_ENABLED=auth_enabled,
        AUTH_COOKIE_SECURE=False,
        _env_file=None,
    )


@pytest.fixture
def fake_auth_router(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate app assembly from the separately implemented HTTP endpoints."""
    module = ModuleType("backend.api.v1.auth")
    router = APIRouter(prefix="/auth", tags=["auth"])

    @router.get("/probe")
    async def auth_probe(response: Response) -> dict[str, bool]:
        response.headers["X-Auth-Probe"] = "true"
        return {"enabled": True}

    module.router = router  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend.api.v1.auth", module)


def test_disabled_auth_keeps_routes_credentials_and_trusted_hosts_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_runtime(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("disabled authentication must not build a runtime")

    monkeypatch.setattr(
        app_module,
        "create_sqlalchemy_persistence_runtime",
        forbidden_runtime,
    )
    application = create_app(
        make_settings(auth_enabled=False),
        model_service=StubModelService(),
        generation_persistence=NoOpGenerationPersistence(),
    )

    assert application.state.auth_service is None
    assert application.state.auth_rate_limiter is None
    assert "/api/v1/auth/probe" not in application.openapi()["paths"]

    response = asyncio.run(
        send_request(
            "GET",
            "/api/health",
            application=application,
            headers={"Host": "untrusted.example"},
        )
    )
    assert response.status_code == 200

    preflight = asyncio.run(
        send_request(
            "OPTIONS",
            "/api/v1/generations",
            application=application,
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
    )
    assert "access-control-allow-credentials" not in preflight.headers


def test_enabled_auth_mounts_router_singletons_and_security_middleware(
    fake_auth_router: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_service = AuthenticationServiceDouble()
    rate_limiter = AuthRateLimiter()

    def forbidden_runtime(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("fully injected services must not open a database")

    monkeypatch.setattr(
        app_module,
        "create_sqlalchemy_persistence_runtime",
        forbidden_runtime,
    )
    application = create_app(
        make_settings(auth_enabled=True),
        model_service=StubModelService(),
        generation_persistence=NoOpGenerationPersistence(),
        auth_service=auth_service,
        auth_rate_limiter=rate_limiter,
    )

    assert application.state.auth_service is auth_service
    assert application.state.auth_rate_limiter is rate_limiter
    assert "/api/v1/auth/probe" in application.openapi()["paths"]

    response = asyncio.run(
        send_request("GET", "/api/v1/auth/probe", application=application)
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-auth-probe"] == "true"

    preflight = asyncio.run(
        send_request(
            "OPTIONS",
            "/api/v1/auth/probe",
            application=application,
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-xhs-csrf",
            },
        )
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == (
        "http://localhost:5173"
    )
    assert preflight.headers["access-control-allow-credentials"] == "true"
    assert preflight.headers["cache-control"] == "no-store"
    allowed_headers = preflight.headers["access-control-allow-headers"].casefold()
    assert "content-type" in allowed_headers
    assert "x-xhs-csrf" in allowed_headers

    rejected_preflight = asyncio.run(
        send_request(
            "OPTIONS",
            "/api/v1/auth/probe",
            application=application,
            headers={
                "Origin": "http://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-xhs-csrf",
            },
        )
    )
    assert rejected_preflight.status_code == 400
    assert "access-control-allow-origin" not in rejected_preflight.headers
    assert rejected_preflight.headers["cache-control"] == "no-store"

    rejected_host = asyncio.run(
        send_request(
            "GET",
            "/api/v1/auth/probe",
            application=application,
            headers={"Host": "untrusted.example"},
        )
    )
    assert rejected_host.status_code == 400
    assert rejected_host.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("inject_generation", "inject_auth", "expected_generation", "expected_auth"),
    [
        (False, False, True, True),
        (True, False, False, True),
        (False, True, True, False),
    ],
)
def test_enabled_auth_builds_one_runtime_with_dynamic_requirements(
    fake_auth_router: None,
    monkeypatch: pytest.MonkeyPatch,
    inject_generation: bool,
    inject_auth: bool,
    expected_generation: bool,
    expected_auth: bool,
) -> None:
    calls: list[dict[str, object]] = []
    auth_store = object()
    runtime = RuntimeDouble(auth_store=auth_store)

    def runtime_factory(_settings: Settings, **kwargs: object) -> RuntimeDouble:
        calls.append(kwargs)
        return runtime

    monkeypatch.setattr(
        app_module,
        "create_sqlalchemy_persistence_runtime",
        runtime_factory,
    )
    injected_generation = (
        NoOpGenerationPersistence() if inject_generation else None
    )
    injected_auth = AuthenticationServiceDouble() if inject_auth else None
    application = create_app(
        make_settings(auth_enabled=True),
        model_service=StubModelService(),
        generation_persistence=injected_generation,
        auth_service=injected_auth,
    )
    limiter = application.state.auth_rate_limiter
    fallback_generation = application.state.generation_persistence

    async def exercise() -> None:
        async with application.router.lifespan_context(application):
            assert runtime.startup_calls == 1
            if expected_generation:
                assert application.state.generation_persistence is runtime.persistence
            else:
                assert application.state.generation_persistence is injected_generation
            if expected_auth:
                assert isinstance(application.state.auth_service, AuthService)
            else:
                assert application.state.auth_service is injected_auth
            assert application.state.auth_rate_limiter is limiter

    asyncio.run(exercise())

    assert calls == [
        {
            "require_generation": expected_generation,
            "require_auth": expected_auth,
        }
    ]
    assert runtime.close_calls == 1
    assert application.state.auth_service is injected_auth
    assert application.state.generation_persistence is fallback_generation


def test_missing_runtime_auth_store_fails_closed_and_disposes(
    fake_auth_router: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = RuntimeDouble(auth_store=None)
    monkeypatch.setattr(
        app_module,
        "create_sqlalchemy_persistence_runtime",
        lambda *_args, **_kwargs: runtime,
    )
    application = create_app(
        make_settings(auth_enabled=True),
        model_service=StubModelService(),
        generation_persistence=NoOpGenerationPersistence(),
    )

    async def exercise() -> DatabaseStartupError:
        with pytest.raises(DatabaseStartupError) as caught:
            async with application.router.lifespan_context(application):
                raise AssertionError("missing auth store must prevent startup")
        return caught.value

    error = asyncio.run(exercise())

    assert str(error) == "database startup verification failed"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert runtime.startup_calls == 1
    assert runtime.close_calls == 1
    assert application.state.auth_service is None
