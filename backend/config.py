"""共享配置（单一配置源：FastAPI B + DB C 共用，避免两套 Settings）。

使用 Pydantic v2 BaseSettings + SQLAlchemy URL.create() 构造连接串，
支持密码含 @ / / / % / : 等特殊字符的正确转义。

导出：
    - Settings：团队共享 BaseSettings（B 和 C 均 import 这一个，禁止再写第二套）
    - get_settings()：单例 getter（懒加载一次 env/.env）
    - build_database_url(settings) -> str：用 SQLAlchemy URL.create() 构造，自动转义特殊字符
    - mask_database_url(url) -> str：基于 SQLAlchemy URL.make_url() 做安全脱敏，密码一律 ***
    - parse_mysql_url(url) -> (user, pwd, host, port, db) | None
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

try:
    from pydantic import Field, model_validator
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


# SQLAlchemy 2.0.25 ~ 2.0.x：URL._make / URL.set / URL.render_as_string
# 向前兼容：优先 make_url()，否则 _make()
def _sa_make_url(url_str: str) -> "_SA_URL":
    if hasattr(_SA_URL, "make_url"):
        return _SA_URL.make_url(url_str)  # type: ignore[attr-defined]
    # 旧版本 fallback：从 engine.URL 里取 make_url
    try:
        from sqlalchemy.engine import make_url as _mk  # type: ignore
        return _mk(url_str)
    except Exception:
        # 最终 fallback：URL._make
        return _SA_URL._make(url_str)  # type: ignore[attr-defined]


def _sa_set(url_obj: "_SA_URL", **kwargs) -> "_SA_URL":
    """跨版本 URL.set() 包装。"""
    if hasattr(url_obj, "set"):
        return url_obj.set(**kwargs)  # type: ignore[call-arg]
    # fallback：用 _replace
    if hasattr(url_obj, "_replace"):
        return url_obj._replace(**kwargs)  # type: ignore[attr-defined]
    # 最老 fallback：重新 _make with dict
    d = url_obj._asdict()  # type: ignore[attr-defined]
    d.update(kwargs)
    return _SA_URL._make(d)  # type: ignore[attr-defined]


_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    """团队共享 Settings（B 的 FastAPI 与 C 的 DB 层均 import 这一份，禁止再写第二套）。"""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- 数据库 ---
    DATABASE_URL: Optional[str] = None
    MYSQL_HOST: str = "127.0.0.1"
    MYSQL_PORT: int = 3306
    MYSQL_DATABASE: str = "xhs_db"
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = ""

    # --- 业务 ---
    DASHSCOPE_API_KEY: str = ""
    MODEL_NAME: str = "qwen-vl-plus"
    UPLOAD_DIR: str = "./uploads"

    # --- 环境 ---
    APP_ENV: str = Field(default="development", alias="FLASK_ENV")
    FLASK_ENV: str = "development"
    FLASK_PORT: int = 5000

    @model_validator(mode="after")
    def _finalize_database_url(self) -> "Settings":
        """model_validator(after)：拿到所有字段后，若 DATABASE_URL 未设置则用 MYSQL_* + URL.create() 拼。"""
        if self.DATABASE_URL:
            return self
        self.DATABASE_URL = build_database_url(self)
        return self


_settings_singleton: Optional[Settings] = None


def get_settings() -> Settings:
    """单例 getter：避免重复读 .env / 环境变量。"""
    global _settings_singleton
    if _settings_singleton is None:
        _settings_singleton = Settings()
    return _settings_singleton


def build_database_url(settings: Settings) -> str:
    """用 SQLAlchemy URL.create() 构造连接串，**自动对 password / user / db 等做 % 转义**。

    解决：密码含 @ / : / / / ? / % / [空格] 时手写拼接会 parse 失败的问题。
    """
    raw = getattr(settings, "DATABASE_URL", None)
    if raw:
        # 兼容直接设置的 URL（但我们仍建议传 MYSQL_* 组合）
        return raw
    if not settings.DATABASE_URL:
        # 构造 MySQL URL（pymysql driver）
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
    return settings.DATABASE_URL


def mask_database_url(url: str) -> str:
    """基于 SQLAlchemy 的密码脱敏，100% 覆盖含特殊字符（@ / % / : 等）的密码。

    为保证脱敏后肉眼可见且 grep 无原密码残留：使用 URL.render_as_string 的
    hide_password=True（输出为 ``***``），并补回被 SA 去掉的 ``:***`` 前缀。
    """
    if not url:
        return ""
    try:
        u = _sa_make_url(url)
        if getattr(u, "password", None):
            # 策略：无密码 render + 正则把 user@ 替换成 user:***@
            rendered = u.render_as_string(hide_password=True)
            import re as _re

            rendered = _re.sub(
                r"^(?P<scheme>[a-zA-Z][\w+\.\-]*)://(?P<user>[^:@/\s]+)@",
                lambda m: f"{m.group('scheme')}://{m.group('user')}:***@",
                rendered,
                count=1,
            )
            return rendered
        return u.render_as_string(hide_password=False)
    except Exception:
        import re as _re

        m = _re.match(
            r"^(?P<scheme>[a-zA-Z][\w+\.\-]*)://(?P<user>[^:@/\s]+):(?P<pwd>[^@/\s]+)@",
            url,
        )
        if not m:
            return url
        scheme, user = m.group("scheme"), m.group("user")
        head_end = m.end()
        return f"{scheme}://{user}:***@{url[head_end:]}"


def parse_mysql_url(url: str) -> Optional[Tuple[str, str, str, int, str]]:
    """基于 SQLAlchemy 的 MySQL URL 解析，user/password 正确 unquote（含 @ / % / : 等特殊字符。"""
    if not url:
        return None
    try:
        u = _sa_make_url(url)
        if not (u.drivername and u.drivername.startswith("mysql")):
            return None
        host = u.host or "127.0.0.1"
        port = int(u.port or 3306)
        user = u.username or ""
        password = u.password or ""
        database = u.database or ""
        return user, password, host, port, database
    except Exception:
        return None


def ensure_uploads_dir(settings: Settings) -> None:
    try:
        os.makedirs(settings.UPLOAD_DIR or "./uploads", exist_ok=True)
    except Exception:
        pass


__all__ = [
    "Settings",
    "get_settings",
    "build_database_url",
    "mask_database_url",
    "parse_mysql_url",
    "ensure_uploads_dir",
]
