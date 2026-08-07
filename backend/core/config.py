"""Validated application settings loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


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

    @model_validator(mode="after")
    def validate_model_phase_timeouts(self) -> "Settings":
        """Give the required Qwen phase at least half of the total deadline."""
        if self.ocr_timeout_seconds > self.model_timeout_seconds / 2:
            raise ValueError(
                "OCR_TIMEOUT_SECONDS must not exceed half of "
                "MODEL_TIMEOUT_SECONDS"
            )
        return self

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
    return Settings()
