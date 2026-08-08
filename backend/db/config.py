"""独立的数据库配置（仅 DB 层使用，不与 B 侧 backend.core.config.Settings 冲突）。

**为什么单独放一份？**
B 侧后端目前没有把其真实 Settings 发布到公共仓库，C 侧无法看到 B 所需的 siliconflow_api_key /
vision_model_name / ocr_model_name / cors_origins / resolved_upload_dir / max_image_size_mb /
max_image_pixels / model_max_image_edge 等字段。为避免重写一套配置破坏 B 的本地项目，
这里保持最小依赖的独立 DatabaseConfig，只负责数据库 URL 构造 + 安全脱敏 + 安全解析。

后续集成方式（由 B 侧在其自己的分支中完成即可）：
    1. 把 DATABASE_URL / MYSQL_HOST / ... 字段追加到 B 的真实 Settings；
    2. 把 `from backend.db.config import DatabaseConfig, get_database_config, ...` 替换为
       `from backend.core.config import settings`，并在调用 init_database(settings) 时
       把 B 的 settings 对象传进来（DatabaseConfig 所需字段 B 的 settings 里也有即可）。

安全：
    MYSQL_PASSWORD 使用 Pydantic SecretStr；repr(Settings) / print 永不输出明文；
    mask_database_url 在 parse 不确定时直接返回固定安全占位符，绝不回原 URL。
"""
from __future__ import annotations

import os
import re as _re
from pathlib import Path
from typing import Optional, Tuple

try:
    from pydantic import Field, SecretStr, field_validator, model_validator
    from pydantic_settings import BaseSettings as _PydanticBaseSettings, SettingsConfigDict
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


# 跨版本 make_url：优先 engine.make_url，其次 URL._make
def _sa_make_url(url_str: str):
    if hasattr(_SA_URL, "make_url"):
        return _SA_URL.make_url(url_str)  # type: ignore[attr-defined]
    try:
        from sqlalchemy.engine import make_url as _mk  # type: ignore
        return _mk(url_str)
    except Exception:
        return _SA_URL._make(url_str)  # type: ignore[attr-defined]


_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

# 固定安全占位符：当 URL 无法被可靠解析时直接返回此字符串，永不回原 URL
INVALID_DB_URL_PLACEHOLDER = "<invalid-database-url>"


class DatabaseConfig(_PydanticBaseSettings):
    """仅数据库层使用的最小化配置（不与 B 的 backend.core.config.Settings 冲突）。

    用法（两种方式都支持）：
        1. 环境变量 / .env 自动加载：
            cfg = get_database_config()
            engine, sf = init_database(cfg)
        2. B 侧显式注入 DATABASE_URL：
            cfg = DatabaseConfig(DATABASE_URL=B_settings.DATABASE_URL, _env_file=None)
            engine, sf = init_database(cfg)
    """

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # 显式 DATABASE_URL（优先级最高，repr=False 防止泄漏）
    DATABASE_URL: Optional[str] = Field(default=None, repr=False)

    # 组合字段（当 DATABASE_URL 为空时拼接）
    MYSQL_HOST: str = "127.0.0.1"
    MYSQL_PORT: int = 3306
    MYSQL_DATABASE: str = "xhs_db"
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)

    @field_validator("MYSQL_PASSWORD", mode="before")
    @classmethod
    def _coerce_secret(cls, v):
        if isinstance(v, SecretStr):
            return v
        return SecretStr("" if v is None else str(v))

    @model_validator(mode="after")
    def _finalize(self) -> "DatabaseConfig":
        if not self.DATABASE_URL:
            # 仅当未显式指定 DATABASE_URL 时组合。B 侧显式注入则保留原样。
            self.DATABASE_URL = build_database_url(self)
        return self


_db_config_singleton: Optional[DatabaseConfig] = None


def get_database_config() -> DatabaseConfig:
    """单例 getter。"""
    global _db_config_singleton
    if _db_config_singleton is None:
        _db_config_singleton = DatabaseConfig()
    return _db_config_singleton


# ---------------------------------------------------------------------------
# URL 构造 / 安全脱敏 / 安全解析
# ---------------------------------------------------------------------------

def build_database_url(cfg: DatabaseConfig) -> str:
    """用 SQLAlchemy URL.create() 构造，保证密码含 @/%/: 时正确 % 转义。"""
    if getattr(cfg, "DATABASE_URL", None):
        return str(cfg.DATABASE_URL)
    # 注意：SecretStr 的 get_secret_value() 在这里只用于构造本地 URL，绝不打印
    pwd = cfg.MYSQL_PASSWORD.get_secret_value() if isinstance(cfg.MYSQL_PASSWORD, SecretStr) else str(cfg.MYSQL_PASSWORD or "")
    sa_url = _SA_URL.create(
        drivername="mysql+pymysql",
        username=cfg.MYSQL_USER or None,
        password=pwd or None,
        host=cfg.MYSQL_HOST or None,
        port=int(cfg.MYSQL_PORT or 3306),
        database=cfg.MYSQL_DATABASE or None,
        query={"charset": "utf8mb4"},
    )
    return sa_url.render_as_string(hide_password=False)


def mask_database_url(url: Optional[str]) -> str:
    """**极度保守** 的数据库 URL 脱敏：
    - 能被 SQLAlchemy 可靠识别：
        - SQLite：直接返回 SA 渲染（无 password 段，不会占位符误报）；
        - 非 SQLite 且存在 password：SA hide_password + 补 :***@ + query 参数二次 mask；
    - 其它 ANY 情况（畸形 URL / 无法判断密码 / parse 异常 / 渲染后仍等于原 URL 且非 SQLite）：
      直接返回固定安全占位符 `INVALID_DB_URL_PLACEHOLDER = <invalid-database-url>`，
      **绝不尝试保留原结构或原 URL**。
    """
    if not url or not isinstance(url, str):
        return INVALID_DB_URL_PLACEHOLDER
    try:
        u = _sa_make_url(url)
        if not getattr(u, "drivername", None):
            return INVALID_DB_URL_PLACEHOLDER
        is_sqlite = str(getattr(u, "drivername", "")).startswith("sqlite")
        if not is_sqlite and not getattr(u, "host", None):
            return INVALID_DB_URL_PLACEHOLDER
        if getattr(u, "password", None) and not is_sqlite:
            rendered = u.render_as_string(hide_password=True)
            rendered = _re.sub(
                r"^(?P<s>[a-zA-Z][\w+\.\-]*)://(?P<u>[^:@/?#\s]+)@",
                lambda m: f"{m.group('s')}://{m.group('u')}:***@",
                rendered,
                count=1,
            )
        else:
            rendered = u.render_as_string(hide_password=True)
        # query 参数二次 mask
        rendered = _re.sub(r"([?&]pass(?:word)?=)[^&\s]+", r"\1***", rendered, flags=_re.IGNORECASE)
        rendered = _re.sub(r"([?&]pwd=)[^&\s]+", r"\1***", rendered, flags=_re.IGNORECASE)
        rendered = _re.sub(r"([?&]secret=)[^&\s]+", r"\1***", rendered, flags=_re.IGNORECASE)
        rendered = _re.sub(r"([?&]token=)[^&\s]+", r"\1***", rendered, flags=_re.IGNORECASE)
        rendered = _re.sub(r"([?&]key=)[^&\s]+", r"\1***", rendered, flags=_re.IGNORECASE)
        # SQLite 特例：即使和原 URL 相同（无密码段）也不占位（本来就没有密码）
        if not is_sqlite and rendered == url:
            return INVALID_DB_URL_PLACEHOLDER
        return rendered
    except Exception:
        return INVALID_DB_URL_PLACEHOLDER


def parse_mysql_url(url: Optional[str]) -> Optional[Tuple[str, str, str, int, str]]:
    """解析 MySQL URL。parse 不确定或非 MySQL → 返回 None（绝不把原 URL 放进异常）。"""
    if not url or not isinstance(url, str):
        return None
    try:
        u = _sa_make_url(url)
        if not (u.drivername and str(u.drivername).startswith("mysql")):
            return None
        if not u.host:
            return None
        host = str(u.host)
        try:
            port = int(u.port or 3306)
        except Exception:
            # 端口非数字（如 notaport）→ parse 失败，拒绝
            return None
        if not (0 < port < 65536):
            return None
        user = str(u.username or "")
        password = str(u.password or "")
        database = str(u.database or "")
        return user, password, host, port, database
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 工具（独立于 B 的 settings，C 自测脚本用）
# ---------------------------------------------------------------------------

def ensure_uploads_dir(upload_dir: str = "./uploads") -> Path:
    p = Path(upload_dir or "./uploads").expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


__all__ = [
    "DatabaseConfig",
    "get_database_config",
    "build_database_url",
    "mask_database_url",
    "parse_mysql_url",
    "ensure_uploads_dir",
    "INVALID_DB_URL_PLACEHOLDER",
]
