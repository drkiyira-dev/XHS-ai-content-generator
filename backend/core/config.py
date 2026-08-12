"""Validated application settings loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, HttpUrl, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
RUNTIME_SECRETS_DIR = Path("/run/secrets")


class Settings(BaseSettings):
    """Runtime settings shared by the API and model service layers."""

    siliconflow_api_key: SecretStr = Field(
        validation_alias="SILICONFLOW_API_KEY",
        description="SiliconFlow credential used only by the backend.",
    )
    siliconflow_base_url: HttpUrl = Field(
        validation_alias="SILICONFLOW_BASE_URL",
        description="Base URL for the SiliconFlow OpenAI-compatible API.",
    )
    vision_model_name: str = Field(
        validation_alias="VISION_MODEL_NAME",
        description="Primary model for image understanding and content generation.",
    )
    ocr_model_name: str = Field(
        validation_alias="OCR_MODEL_NAME",
        description="Best-effort OCR model run before the primary vision model.",
    )
    model_timeout_seconds: int = Field(
        default=60,
        ge=1,
        le=300,
        validation_alias="MODEL_TIMEOUT_SECONDS",
    )
    ocr_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        le=60,
        validation_alias="OCR_TIMEOUT_SECONDS",
        description="Best-effort OCR phase timeout within the total model deadline.",
    )
    cors_allow_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        validation_alias="CORS_ALLOW_ORIGINS",
        description="Comma-separated browser origins allowed during development.",
    )
    upload_dir: Path = Field(
        default=Path("uploads"),
        validation_alias="UPLOAD_DIR",
    )
    max_image_size_mb: int = Field(
        default=10,
        ge=1,
        le=10,
        validation_alias="MAX_IMAGE_SIZE_MB",
    )
    max_image_pixels: int = Field(
        default=50_000_000,
        ge=1,
        le=50_000_000,
        validation_alias="MAX_IMAGE_PIXELS",
    )
    model_max_image_edge: int = Field(
        default=3584,
        ge=1,
        le=3584,
        validation_alias="MODEL_MAX_IMAGE_EDGE",
    )
    database_enabled: bool = Field(
        default=False,
        validation_alias="DATABASE_ENABLED",
        description="Explicit opt-in for MySQL-backed generation persistence.",
    )
    database_url: SecretStr | None = Field(
        default=None,
        validation_alias="DATABASE_URL",
        description="MySQL SQLAlchemy URL; never logged or returned by the API.",
    )
    database_connect_timeout_seconds: int = Field(
        default=5,
        ge=1,
        le=30,
        validation_alias="DATABASE_CONNECT_TIMEOUT_SECONDS",
    )
    database_read_timeout_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        validation_alias="DATABASE_READ_TIMEOUT_SECONDS",
    )
    database_write_timeout_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        validation_alias="DATABASE_WRITE_TIMEOUT_SECONDS",
    )
    database_pool_size: int = Field(
        default=5,
        ge=1,
        le=20,
        validation_alias="DATABASE_POOL_SIZE",
    )
    database_max_overflow: int = Field(
        default=5,
        ge=0,
        le=20,
        validation_alias="DATABASE_MAX_OVERFLOW",
    )
    database_pool_timeout_seconds: int = Field(
        default=5,
        ge=1,
        le=60,
        validation_alias="DATABASE_POOL_TIMEOUT_SECONDS",
    )
    database_pool_recycle_seconds: int = Field(
        default=1800,
        ge=60,
        le=7200,
        validation_alias="DATABASE_POOL_RECYCLE_SECONDS",
    )
    database_tls_ca: Path | None = Field(
        default=None,
        validation_alias="DATABASE_TLS_CA",
        description="CA certificate required for a non-loopback MySQL host.",
    )
    auth_cookie_secure: bool = Field(
        default=False,
        validation_alias="AUTH_COOKIE_SECURE",
        description="Use a Secure __Host- cookie outside local HTTP development.",
    )
    auth_enabled: bool = Field(
        default=False,
        validation_alias="AUTH_ENABLED",
        description="Explicit opt-in for local account authentication routes.",
    )

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )

    @field_validator("siliconflow_api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        """Reject a missing or whitespace-only key without exposing it."""
        secret = value.get_secret_value().strip()
        if not secret:
            raise ValueError("SILICONFLOW_API_KEY must not be empty")
        return SecretStr(secret)

    @field_validator("ocr_timeout_seconds")
    @classmethod
    def validate_model_phase_timeouts(
        cls,
        value: float,
        info: ValidationInfo,
    ) -> float:
        """Give the required Qwen phase at least half of the total deadline."""
        model_timeout_seconds = info.data.get("model_timeout_seconds")
        if (
            isinstance(model_timeout_seconds, int)
            and value > model_timeout_seconds / 2
        ):
            raise ValueError(
                "OCR_TIMEOUT_SECONDS must not exceed half of "
                "MODEL_TIMEOUT_SECONDS"
            )
        return value

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: object) -> object:
        """Treat a blank template value as absent without exposing a URL."""
        if isinstance(value, SecretStr):
            return value if value.get_secret_value().strip() else None
        if isinstance(value, str):
            normalized = value.strip()
            return SecretStr(normalized) if normalized else None
        return None

    @field_validator("database_tls_ca", mode="before")
    @classmethod
    def normalize_database_tls_ca(cls, value: object) -> object:
        """Treat the optional blank template value as no CA path."""
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("auth_enabled")
    @classmethod
    def validate_auth_configuration(
        cls,
        value: bool,
        info: ValidationInfo,
    ) -> bool:
        """Fail closed without retaining a database URL in validation input."""
        if not value:
            return value
        if info.data.get("database_enabled") is not True:
            raise ValueError("AUTH_ENABLED requires DATABASE_ENABLED")

        cors_value = info.data.get("cors_allow_origins")
        cookie_secure = info.data.get("auth_cookie_secure") is True
        origins = cors_value.split(",") if isinstance(cors_value, str) else []
        if cookie_secure:
            valid_origins = bool(origins) and all(
                urlsplit(origin).scheme == "https" for origin in origins
            )
        else:
            valid_origins = bool(origins) and all(
                urlsplit(origin).hostname in {"localhost", "127.0.0.1"}
                for origin in origins
            )
        if not valid_origins:
            raise ValueError("authentication CORS origins are not authorized")
        return value

    @field_validator("siliconflow_base_url")
    @classmethod
    def validate_siliconflow_base_url(cls, value: HttpUrl) -> HttpUrl:
        """Keep the backend credential scoped to the official HTTPS API."""
        if (
            value.scheme != "https"
            or value.host != "api.siliconflow.cn"
            or value.username is not None
            or value.password is not None
            or value.query is not None
            or value.fragment is not None
            or value.path.rstrip("/") != "/v1"
        ):
            raise ValueError(
                "SILICONFLOW_BASE_URL must be https://api.siliconflow.cn/v1"
            )
        return value

    @field_validator("vision_model_name", "ocr_model_name")
    @classmethod
    def normalize_model_name(cls, value: str) -> str:
        """Trim each model identifier and reject an empty value."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("model name must not be empty")
        return normalized

    @field_validator("cors_allow_origins")
    @classmethod
    def normalize_cors_origins(cls, value: str) -> str:
        """Validate and normalize a comma-separated list of browser origins."""
        origins = [origin.strip().rstrip("/") for origin in value.split(",")]
        if not origins or any(not origin for origin in origins):
            raise ValueError("CORS_ALLOW_ORIGINS must contain at least one origin")

        for origin in origins:
            parsed = urlsplit(origin)
            try:
                parsed_port = parsed.port
            except ValueError as error:
                raise ValueError("CORS_ALLOW_ORIGINS contains an invalid port") from error
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise ValueError(
                    "CORS_ALLOW_ORIGINS entries must be HTTP(S) origins without paths"
                )
            _ = parsed_port

        return ",".join(dict.fromkeys(origins))

    @property
    def cors_origins(self) -> tuple[str, ...]:
        """Return normalized origins in the form expected by FastAPI."""
        return tuple(self.cors_allow_origins.split(","))

    @property
    def resolved_upload_dir(self) -> Path:
        """Resolve relative upload storage against the repository root."""
        if self.upload_dir.is_absolute():
            return self.upload_dir
        return PROJECT_ROOT / self.upload_dir

    @property
    def resolved_database_tls_ca(self) -> Path:
        """Resolve the optional database CA path against the repository root."""
        if self.database_tls_ca is None:
            raise RuntimeError("database TLS CA is not configured")
        if self.database_tls_ca.is_absolute():
            return self.database_tls_ca
        return PROJECT_ROOT / self.database_tls_ca

    @property
    def auth_cookie_name(self) -> str:
        """Return a fixed cookie name appropriate for the selected transport."""
        return "__Host-xhs_session" if self.auth_cookie_secure else "xhs_session_local"

    @property
    def auth_cookie_path(self) -> str:
        """Keep the local cookie away from ordinary Vite page and HMR requests."""
        return "/" if self.auth_cookie_secure else "/api/v1"

    @field_validator("upload_dir", mode="before")
    @classmethod
    def validate_upload_dir(cls, value: object) -> object:
        """Reject an empty upload directory while preserving Path parsing."""
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("UPLOAD_DIR must not be empty")
            return normalized
        return value


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process after the environment is configured."""
    secrets_dir = RUNTIME_SECRETS_DIR if RUNTIME_SECRETS_DIR.is_dir() else None
    return Settings(_secrets_dir=secrets_dir)
