"""Tests for secure environment-based backend configuration."""

import asyncio
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.core.config import ENV_FILE, PROJECT_ROOT, Settings, get_settings
from backend.services.persistence.runtime import (
    DatabaseStartupError,
    create_sqlalchemy_persistence_runtime,
)


SETTINGS_ENV_NAMES = (
    "SILICONFLOW_API_KEY",
    "SILICONFLOW_BASE_URL",
    "VISION_MODEL_NAME",
    "OCR_MODEL_NAME",
    "MODEL_TIMEOUT_SECONDS",
    "OCR_TIMEOUT_SECONDS",
    "CORS_ALLOW_ORIGINS",
    "UPLOAD_DIR",
    "MAX_IMAGE_SIZE_MB",
    "MAX_IMAGE_PIXELS",
    "MODEL_MAX_IMAGE_EDGE",
    "DATABASE_ENABLED",
    "DATABASE_URL",
    "DATABASE_CONNECT_TIMEOUT_SECONDS",
    "DATABASE_READ_TIMEOUT_SECONDS",
    "DATABASE_WRITE_TIMEOUT_SECONDS",
    "DATABASE_POOL_SIZE",
    "DATABASE_MAX_OVERFLOW",
    "DATABASE_POOL_TIMEOUT_SECONDS",
    "DATABASE_POOL_RECYCLE_SECONDS",
    "DATABASE_TLS_CA",
)
SETTINGS_ENV_NAMES_CASEFOLDED = {name.casefold() for name in SETTINGS_ENV_NAMES}


def set_required_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide safe test-only values for required settings."""
    for existing_name in tuple(os.environ):
        if existing_name.casefold() in SETTINGS_ENV_NAMES_CASEFOLDED:
            monkeypatch.delenv(existing_name, raising=False)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-secret-key")
    monkeypatch.setenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
    monkeypatch.setenv("VISION_MODEL_NAME", "qwen-test-model")
    monkeypatch.setenv("OCR_MODEL_NAME", "paddle-test-model")
    monkeypatch.setenv("DATABASE_ENABLED", "false")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DATABASE_TLS_CA", "")


def test_loads_required_values_and_contract_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required_environment(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.siliconflow_api_key.get_secret_value() == "test-secret-key"
    assert str(settings.siliconflow_base_url) == "https://api.siliconflow.cn/v1"
    assert settings.vision_model_name == "qwen-test-model"
    assert settings.ocr_model_name == "paddle-test-model"
    assert settings.model_timeout_seconds == 60
    assert settings.ocr_timeout_seconds == 15.0
    assert settings.cors_origins == (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    assert settings.upload_dir == Path("uploads")
    assert settings.max_image_size_mb == 10
    assert settings.max_image_pixels == 50_000_000
    assert settings.model_max_image_edge == 3584
    assert settings.database_enabled is False
    assert settings.database_url is None
    assert settings.database_connect_timeout_seconds == 5
    assert settings.database_read_timeout_seconds == 30
    assert settings.database_write_timeout_seconds == 30
    assert settings.database_pool_size == 5
    assert settings.database_max_overflow == 5
    assert settings.database_pool_timeout_seconds == 5
    assert settings.database_pool_recycle_seconds == 1800
    assert settings.database_tls_ca is None


@pytest.mark.parametrize(
    "missing_name",
    [
        "SILICONFLOW_API_KEY",
        "SILICONFLOW_BASE_URL",
        "VISION_MODEL_NAME",
        "OCR_MODEL_NAME",
    ],
)
def test_requires_key_and_model_name(
    monkeypatch: pytest.MonkeyPatch,
    missing_name: str,
) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.delenv(missing_name)

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)

    assert missing_name in str(error.value)


@pytest.mark.parametrize(
    "blank_name",
    ["SILICONFLOW_API_KEY", "VISION_MODEL_NAME", "OCR_MODEL_NAME"],
)
def test_rejects_blank_required_values(
    monkeypatch: pytest.MonkeyPatch,
    blank_name: str,
) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv(blank_name, "   ")

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)

    assert blank_name in str(error.value)


def test_masks_api_key_in_settings_representation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required_environment(monkeypatch)

    settings = Settings(_env_file=None)

    assert "test-secret-key" not in repr(settings)
    assert str(settings.siliconflow_api_key) == "**********"


def test_masks_database_url_everywhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required_environment(monkeypatch)
    private_url = (
        "mysql+pymysql://xhs_app:private-db-password@"
        "127.0.0.1:3306/xhs_ai_test"
    )
    monkeypatch.setenv("DATABASE_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", private_url)

    settings = Settings(_env_file=None)

    assert "private-db-password" not in repr(settings)
    assert private_url not in repr(settings)
    assert "private-db-password" not in settings.model_dump_json()
    assert str(settings.database_url) == "**********"


def test_database_url_is_required_only_after_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv("DATABASE_ENABLED", "true")

    settings = Settings(_env_file=None)
    with pytest.raises(DatabaseStartupError) as error:
        create_sqlalchemy_persistence_runtime(settings)

    assert str(error.value) == "database startup verification failed"
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite+pysqlite:///unsafe.sqlite3",
        "mysql://xhs_app:secret@127.0.0.1/xhs_ai_test",
        "mysql+pymysql://root:secret@127.0.0.1/xhs_ai_test",
        "mysql+pymysql://xhs_app@127.0.0.1/xhs_ai_test",
        "mysql+pymysql://xhs_app:secret@127.0.0.1",
        (
            "mysql+pymysql://xhs_app:secret@127.0.0.1/xhs_ai_test"
            "?local_infile=1"
        ),
    ],
)
def test_rejects_unsafe_database_urls_without_exposing_them(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv("DATABASE_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", database_url)

    settings = Settings(_env_file=None)
    with pytest.raises(DatabaseStartupError) as error:
        create_sqlalchemy_persistence_runtime(settings)

    rendered_error = repr(error.value)
    assert database_url not in rendered_error
    assert "secret" not in rendered_error
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_remote_database_requires_an_existing_tls_ca(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv("DATABASE_ENABLED", "true")
    monkeypatch.setenv(
        "DATABASE_URL",
        "mysql+pymysql://xhs_app:secret@db.example.com/xhs_ai_test",
    )

    settings_without_ca = Settings(_env_file=None)
    with pytest.raises(DatabaseStartupError) as missing_ca:
        create_sqlalchemy_persistence_runtime(settings_without_ca)
    assert str(missing_ca.value) == "database startup verification failed"

    ca_path = tmp_path / "mysql-ca.pem"
    ca_path.write_text("test-only-ca", encoding="utf-8")
    monkeypatch.setenv("DATABASE_TLS_CA", str(ca_path))

    settings = Settings(_env_file=None)
    runtime = create_sqlalchemy_persistence_runtime(settings)
    asyncio.run(runtime.aclose())

    assert settings.resolved_database_tls_ca == ca_path


def test_unrelated_validation_errors_do_not_retain_any_secret_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required_environment(monkeypatch)
    api_secret = "private-provider-key-in-settings"
    database_secret = "private-database-password-in-settings"
    monkeypatch.setenv("SILICONFLOW_API_KEY", api_secret)
    monkeypatch.setenv("MODEL_TIMEOUT_SECONDS", "20")
    monkeypatch.setenv("OCR_TIMEOUT_SECONDS", "11")
    monkeypatch.setenv("DATABASE_ENABLED", "true")
    monkeypatch.setenv(
        "DATABASE_URL",
        f"mysql+pymysql://xhs_app:{database_secret}@127.0.0.1/xhs_ai_test",
    )

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)

    rendered_errors = repr(caught.value.errors())
    rendered_json = caught.value.json()
    for secret in (api_secret, database_secret):
        assert secret not in rendered_errors
        assert secret not in rendered_json


def test_hides_invalid_secret_input_from_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required_environment(monkeypatch)
    sensitive_value = "must-not-appear-in-errors"

    with pytest.raises(ValidationError) as error:
        Settings(
            SILICONFLOW_API_KEY={"token": sensitive_value},
            _env_file=None,
        )

    assert sensitive_value not in str(error.value)


def test_settings_are_immutable(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required_environment(monkeypatch)
    settings = Settings(_env_file=None)

    with pytest.raises(ValidationError):
        settings.model_timeout_seconds = 999

    assert settings.model_timeout_seconds == 60


def test_get_settings_returns_one_immutable_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required_environment(monkeypatch)
    get_settings.cache_clear()

    try:
        first = get_settings()
        monkeypatch.setenv("VISION_MODEL_NAME", "changed-after-first-load")
        second = get_settings()

        assert first is second
        assert second.vision_model_name == "qwen-test-model"
    finally:
        get_settings.cache_clear()


def test_normalizes_model_name_and_upload_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv("VISION_MODEL_NAME", "  qwen-test-model  ")
    monkeypatch.setenv("OCR_MODEL_NAME", "  paddle-test-model  ")
    monkeypatch.setenv(
        "CORS_ALLOW_ORIGINS",
        " https://frontend.example.com/, http://localhost:5173 ",
    )
    monkeypatch.setenv("UPLOAD_DIR", "  runtime/uploads  ")

    settings = Settings(_env_file=None)

    assert settings.vision_model_name == "qwen-test-model"
    assert settings.ocr_model_name == "paddle-test-model"
    assert settings.cors_origins == (
        "https://frontend.example.com",
        "http://localhost:5173",
    )
    assert settings.upload_dir == Path("runtime/uploads")


def test_default_dotenv_path_is_bound_to_project_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert ENV_FILE == PROJECT_ROOT / ".env"
    assert ENV_FILE.is_absolute()
    assert Settings.model_config["env_file"] == ENV_FILE


def test_loads_an_explicit_dotenv_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.delenv("SILICONFLOW_API_KEY")
    monkeypatch.delenv("SILICONFLOW_BASE_URL")
    monkeypatch.delenv("VISION_MODEL_NAME")
    monkeypatch.delenv("OCR_MODEL_NAME")
    dotenv_file = tmp_path / "test.env"
    dotenv_file.write_text(
        "SILICONFLOW_API_KEY=dotenv-test-key\n"
        "SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1\n"
        "VISION_MODEL_NAME=dotenv-qwen-model\n"
        "OCR_MODEL_NAME=dotenv-paddle-model\n"
        "MODEL_TIMEOUT_SECONDS=45\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=dotenv_file)

    assert settings.siliconflow_api_key.get_secret_value() == "dotenv-test-key"
    assert str(settings.siliconflow_base_url) == "https://api.siliconflow.cn/v1"
    assert settings.vision_model_name == "dotenv-qwen-model"
    assert settings.ocr_model_name == "dotenv-paddle-model"
    assert settings.model_timeout_seconds == 45


def test_rejects_invalid_siliconflow_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv("SILICONFLOW_BASE_URL", "not-a-url")

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)

    assert "SILICONFLOW_BASE_URL" in str(error.value)


def test_ocr_timeout_must_leave_time_for_qwen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv("MODEL_TIMEOUT_SECONDS", "20")
    monkeypatch.setenv("OCR_TIMEOUT_SECONDS", "11")

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)

    assert "OCR_TIMEOUT_SECONDS must not exceed half" in str(error.value)


@pytest.mark.parametrize(
    "value",
    [
        "http://api.siliconflow.cn/v1",
        "https://example.com/v1",
        "https://user:password@api.siliconflow.cn/v1",
        "https://api.siliconflow.cn/v1?target=other",
        "https://api.siliconflow.cn/v1#fragment",
        "https://api.siliconflow.cn/v2",
    ],
)
def test_rejects_base_urls_that_could_leak_the_provider_key(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv("SILICONFLOW_BASE_URL", value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "*",
        "localhost:5173",
        "http://localhost:not-a-port",
        "https://example.com/path",
        "https://a.test,,https://b.test",
    ],
)
def test_rejects_invalid_cors_origins(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", value)

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)

    assert "CORS_ALLOW_ORIGINS" in str(error.value)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MODEL_TIMEOUT_SECONDS", "0"),
        ("MODEL_TIMEOUT_SECONDS", "301"),
        ("MAX_IMAGE_SIZE_MB", "0"),
        ("MAX_IMAGE_SIZE_MB", "11"),
        ("MAX_IMAGE_PIXELS", "0"),
        ("MAX_IMAGE_PIXELS", "50000001"),
        ("MODEL_MAX_IMAGE_EDGE", "0"),
        ("MODEL_MAX_IMAGE_EDGE", "3585"),
        ("UPLOAD_DIR", "   "),
    ],
)
def test_rejects_values_outside_the_frozen_contract(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
