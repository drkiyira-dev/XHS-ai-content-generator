"""共享单一配置源（B 的 FastAPI 后端与 C 的数据库层统一 import 此处，禁止第二套 Settings）。

职责：
1. 用 Pydantic v2 BaseSettings 统一管理 env/.env；
2. 补齐 B 端必需的全部配置（SiliconFlow、Qwen、OCR、超时、CORS、图片限制、端口、workers、日志等）；
3. 数据库 URL 一律走 SQLAlchemy URL.create() 构造，保证密码含 @ / % / : / / 等特殊字符时正确转义；
4. mask_database_url / parse_mysql_url 集中在此：失败时绝不回原 URL，防止泄漏。
"""
from __future__ import annotations

import os
import re as _re
from pathlib import Path
from typing import List, Optional, Tuple

try:
    from pydantic import Field, field_validator, model_validator
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "缺少依赖：pydantic>=2.6, pydantic-settings>=2。请先运行：pip install -r requirements.txt"
    ) from e

try:
    from sqlalchemy import URL as _SA_URL
except ImportError:  # pragma: no cover
    raise SystemExit(
        "缺少依赖：SQLAlchemy>=2.0.25。请先运行：pip install -r requirements.txt"
    )


# ---------------------------------------------------------------------------
# SQLAlchemy URL 跨版本包装（2.0.x：优先 engine.make_url，否则 URL._make）
# ---------------------------------------------------------------------------

def _sa_make_url(url_str: str):
    if hasattr(_SA_URL, "make_url"):
        return _SA_URL.make_url(url_str)  # type: ignore[attr-defined]
    try:
        from sqlalchemy.engine import make_url as _mk  # type: ignore
        return _mk(url_str)
    except Exception:
        return _SA_URL._make(url_str)  # type: ignore[attr-defined]


_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


# ---------------------------------------------------------------------------
# Settings（单一来源，含 B 端全部必需字段 + C DB 字段）
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    """B + C 统一共享 Settings。**禁止定义第二套 Settings。**

    B 端使用：from backend.core.config import Settings, get_settings
    C 端使用：同上。
    """

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ------------------------------------------------------------------ DB
    DATABASE_URL: Optional[str] = None
    MYSQL_HOST: str = "127.0.0.1"
    MYSQL_PORT: int = 3306
    MYSQL_DATABASE: str = "xhs_db"
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = ""

    # ------------------------------------------------------------ LLM 通用
    # 默认模型提供商；B 端保留多家提供商（DashScope / SiliconFlow / Qwen 本地/兼容）。
    LLM_PROVIDER: str = "dashscope"  # dashscope | siliconflow | openai_compatible | qwen_direct

    # DashScope（阿里 Qwen-VL / Qwen-Turbo 等，B 侧默认）
    DASHSCOPE_API_KEY: str = ""
    MODEL_NAME: str = "qwen-vl-plus"

    # SiliconFlow（B 侧新增：兼容 OpenAI 协议的第三方）
    SILICONFLOW_API_KEY: str = ""
    SILICONFLOW_BASE_URL: str = "https://api.siliconflow.cn/v1"
    SILICONFLOW_MODEL: str = "Qwen/Qwen2-VL-72B-Instruct"
    SILICONFLOW_TIMEOUT: float = 60.0

    # Qwen 直接模式（阿里云百炼 API，与 DashScope 不同的 base url 场景）
    QWEN_API_KEY: str = ""
    QWEN_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    QWEN_MODEL: str = "qwen-vl-plus"
    QWEN_TIMEOUT: float = 60.0

    # OpenAI 兼容（兜底：任何兼容 /v1/chat/completions 的服务）
    OPENAI_COMPAT_API_KEY: str = ""
    OPENAI_COMPAT_BASE_URL: str = ""
    OPENAI_COMPAT_MODEL: str = ""
    OPENAI_COMPAT_TIMEOUT: float = 60.0

    # -------------------------------------------------------------- OCR
    OCR_PROVIDER: str = "none"  # none | rapidocr | paddle
    RAPIDOCR_LANG: str = "ch"
    OCR_TIMEOUT: float = 30.0
    # OCR 可接受的最小分辨率，避免噪声图
    OCR_MIN_IMAGE_SIZE: int = 64

    # -------------------------------------------------------------- 超时
    # 整体生成超时（生成任务最长执行秒数，B 侧 worker 会打断）
    GENERATION_TIMEOUT: float = 180.0
    # 单次 HTTP 调用默认超时（秒）
    HTTP_TIMEOUT: float = 30.0
    # 上传文件读取超时
    UPLOAD_READ_TIMEOUT: float = 30.0

    # -------------------------------------------------------------- CORS
    CORS_ORIGINS: str = "*"  # 多个用 "," 或空白分隔；B 侧会 split 成 list
    CORS_CREDENTIALS: bool = True
    CORS_METHODS: str = "*"
    CORS_HEADERS: str = "*"

    # -------------------------------------------------------------- 上传
    UPLOAD_DIR: str = "./uploads"
    # 单张图片最大字节（默认 8MB）
    MAX_IMAGE_SIZE_BYTES: int = 8 * 1024 * 1024
    # 允许的 MIME types
    ALLOWED_IMAGE_MIMES: str = "image/jpeg,image/png,image/webp,image/gif"
    # 允许的扩展名（兜底校验，与 MIME 双保险）
    ALLOWED_IMAGE_EXTS: str = ".jpg,.jpeg,.png,.webp,.gif"
    # 图片最大边（像素，超过则按比例缩放再送模型；0 表示不限制）
    MAX_IMAGE_SIDE_PX: int = 2048

    # -------------------------------------------------------------- 服务
    APP_ENV: str = Field(default="development", alias="FLASK_ENV")
    FLASK_ENV: str = "development"
    FLASK_PORT: int = 5000
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_WORKERS: int = 1
    API_RELOAD: bool = False

    # -------------------------------------------------------------- 日志
    LOG_LEVEL: str = "INFO"
    LOG_DIR: str = "./logs"
    LOG_JSON: bool = False

    # -------------------------------------------------------------- 限流（简化配置）
    RATE_LIMIT_ENABLED: bool = False
    # 每用户每分钟生成次数（0 表示不限制）
    RATE_LIMIT_PER_MINUTE: int = 30
    RATE_LIMIT_STORAGE: str = "memory"  # memory | redis
    RATE_LIMIT_REDIS_URL: str = ""

    @model_validator(mode="after")
    def _finalize_database_url(self) -> "Settings":
        if not self.DATABASE_URL:
            self.DATABASE_URL = build_database_url(self)
        return self

    @field_validator("CORS_ORIGINS", "CORS_METHODS", "CORS_HEADERS", "ALLOWED_IMAGE_MIMES", "ALLOWED_IMAGE_EXTS", mode="after")
    @classmethod
    def _strip_str_fields(cls, v: str) -> str:
        return (v or "").strip()

    # --- helpers（B 侧常用拆分后的列表形式）-------------------------------

    def cors_origin_list(self) -> List[str]:
        raw = (self.CORS_ORIGINS or "").strip()
        if not raw or raw == "*":
            return ["*"]
        return [o.strip() for o in _re.split(r"[,\s]+", raw) if o.strip()]

    def allowed_image_mime_list(self) -> List[str]:
        raw = (self.ALLOWED_IMAGE_MIMES or "").strip()
        return [m.strip().lower() for m in raw.split(",") if m.strip()]

    def allowed_image_ext_set(self) -> set:
        raw = (self.ALLOWED_IMAGE_EXTS or "").strip()
        return {e.strip().lower() for e in raw.split(",") if e.strip()}


_settings_singleton: Optional[Settings] = None


def get_settings() -> Settings:
    """单例 getter（避免重复解析 .env / 环境变量）。"""
    global _settings_singleton
    if _settings_singleton is None:
        _settings_singleton = Settings()
    return _settings_singleton


# ---------------------------------------------------------------------------
# 数据库 URL：构造 / 脱敏 / 解析（Single Source of Truth）
# ---------------------------------------------------------------------------

def build_database_url(settings: Settings) -> str:
    """用 SQLAlchemy `URL.create()` 构造连接串，自动 % 转义特殊字符。"""
    raw = getattr(settings, "DATABASE_URL", None)
    if raw:
        return raw
    # 默认 MySQL + PyMySQL（SQLite 场景直接把 DATABASE_URL=sqlite:///... 即可）
    sa_url = _SA_URL.create(
        drivername="mysql+pymysql",
        username=settings.MYSQL_USER or None,
        password=settings.MYSQL_PASSWORD or None,
        host=settings.MYSQL_HOST or None,
        port=int(settings.MYSQL_PORT or 3306),
        database=settings.MYSQL_DATABASE or None,
        query={"charset": "utf8mb4"},
    )
    return sa_url.render_as_string(hide_password=False)


# 安全占位符：mask_database_url 任何失败都绝不回原 URL
_SAFE_MASK_PLACEHOLDER = "***masked***"


def _fallback_mask(url: str) -> str:
    """通用 fallback：绝不回原 URL。"""
    # 1. 尽力匹配 scheme://user:pwd@... 做脱敏
    m = _re.match(
        r"^(?P<scheme>[a-zA-Z][\w+\.\-]*)://(?P<user>[^:@/?#\s]+):(?P<pwd>[^@/?#\s]+)@",
        url or "",
    )
    if m:
        scheme = m.group("scheme")
        user = m.group("user")
        rest_start = m.end()
        rest = url[rest_start:] or ""
        # 对 rest 里再发现的疑似 password token（形如 ?password=xx）全部 mask
        rest = _re.sub(r"([?&]pass(?:word)?=)([^&\s]+)", r"\1***", rest, flags=_re.IGNORECASE)
        rest = _re.sub(r"([?&]pwd=)([^&\s]+)", r"\1***", rest, flags=_re.IGNORECASE)
        rest = _re.sub(r"([?&]secret=)([^&\s]+)", r"\1***", rest, flags=_re.IGNORECASE)
        return f"{scheme}://{user}:***@{rest}" if rest else f"{scheme}://{user}:***@"
    # 2. scheme://user@host 无密码场景：去掉 query 里的敏感参数
    m2 = _re.match(r"^(?P<scheme>[a-zA-Z][\w+\.\-]*)://(?P<body>[^?#\s]+)(?P<rest>.*)$", url or "")
    if m2:
        scheme = m2.group("scheme")
        body = m2.group("body")
        rest = m2.group("rest") or ""
        rest = _re.sub(r"([?&]pass(?:word)?=)([^&\s]+)", r"\1***", rest, flags=_re.IGNORECASE)
        rest = _re.sub(r"([?&]pwd=)([^&\s]+)", r"\1***", rest, flags=_re.IGNORECASE)
        rest = _re.sub(r"([?&]secret=)([^&\s]+)", r"\1***", rest, flags=_re.IGNORECASE)
        rest = _re.sub(r"([?&]token=)([^&\s]+)", r"\1***", rest, flags=_re.IGNORECASE)
        rest = _re.sub(r"([?&]key=)([^&\s]+)", r"\1***", rest, flags=_re.IGNORECASE)
        return f"{scheme}://{body}{rest}"
    # 3. 完全无法识别：返回安全占位符（原 URL 绝不泄漏）
    return _SAFE_MASK_PLACEHOLDER


def mask_database_url(url: str) -> str:
    """基于 SQLAlchemy 的密码脱敏。**无论任何异常，绝不返回原 URL。**

    保证：
    - 成功路径：`driver://user:***@host:port/db?...`（肉眼可见，grep 不到原密码）；
    - 失败路径：走 `_fallback_mask()`，最多只保留 scheme + host，对 query 里的
      password/pwd/secret/token/key 一律 mask，最坏情况返回安全占位符 `***masked***`。
    """
    if not url:
        return ""
    try:
        u = _sa_make_url(url)
        # 先让 SA 做标准 hide_password 输出，保证密码位一定被脱敏
        rendered = u.render_as_string(hide_password=True)
        # 把 user@ 替换成 user:***@，便于肉眼识别（不改变任何密码信息）
        if getattr(u, "password", None):
            rendered = _re.sub(
                r"^(?P<scheme>[a-zA-Z][\w+\.\-]*)://(?P<user>[^:@/?#\s]+)@",
                lambda m: f"{m.group('scheme')}://{m.group('user')}:***@",
                rendered,
                count=1,
            )
        # 再对 query 里可能残留的敏感参数兜底 mask（双保险）
        rendered = _re.sub(r"([?&]pass(?:word)?=)([^&\s]+)", r"\1***", rendered, flags=_re.IGNORECASE)
        rendered = _re.sub(r"([?&]pwd=)([^&\s]+)", r"\1***", rendered, flags=_re.IGNORECASE)
        rendered = _re.sub(r"([?&]secret=)([^&\s]+)", r"\1***", rendered, flags=_re.IGNORECASE)
        rendered = _re.sub(r"([?&]token=)([^&\s]+)", r"\1***", rendered, flags=_re.IGNORECASE)
        rendered = _re.sub(r"([?&]key=)([^&\s]+)", r"\1***", rendered, flags=_re.IGNORECASE)
        if rendered == url and bool(getattr(u, "password", None)):
            # SA 没替换成功，说明密码写入了其它位置；强制走通用 fallback，绝不回原
            return _fallback_mask(url)
        return rendered
    except Exception:
        # 任何解析失败：强制 fallback，绝不回原 URL
        return _fallback_mask(url)


def parse_mysql_url(url: str) -> Optional[Tuple[str, str, str, int, str]]:
    """基于 SQLAlchemy 解析 MySQL URL，保证 user/password 正确 unquote。

    返回 (user, password, host, port, database)，解析失败或非 MySQL 返回 None。
    异常路径只返回 None，**绝不 raise 出原 URL**。
    """
    if not url:
        return None
    try:
        u = _sa_make_url(url)
        if not (u.drivername and str(u.drivername).startswith("mysql")):
            return None
        host = str(u.host or "127.0.0.1")
        port = int(u.port or 3306)
        user = str(u.username or "")
        password = str(u.password or "")
        database = str(u.database or "")
        return user, password, host, port, database
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def ensure_uploads_dir(settings: Settings) -> Path:
    """创建 UPLOAD_DIR（存在则跳过），返回绝对路径。"""
    p = Path(settings.UPLOAD_DIR or "./uploads").expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def ensure_logs_dir(settings: Settings) -> Path:
    p = Path(settings.LOG_DIR or "./logs").expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


__all__ = [
    "Settings",
    "get_settings",
    "build_database_url",
    "mask_database_url",
    "parse_mysql_url",
    "ensure_uploads_dir",
    "ensure_logs_dir",
]
