"""数据库配置（FastAPI + Pydantic v2 Settings）。

敏感配置全部来自环境变量或本地 .env（.gitignore 已阻止 .env 入库），
代码中不存在任何明文密码 / API Key / sk-* 真实密钥。

导出：
- Settings：Pydantic v2 BaseSettings，支持从 env / .env / 显式参数加载
- get_settings()：单例获取 Settings（懒加载）
- mask_database_url(url)：打印日志前对 DATABASE_URL 中的密码做脱敏

环境变量：
    首选：
        DATABASE_URL            - SQLAlchemy 完整连接串
                                  例：mysql+pymysql://u:p@127.0.0.1:3306/xhs_db?charset=utf8mb4
                                      sqlite:///./xhs.db
    DATABASE_URL 未设置时，自动用以下变量拼接：
        MYSQL_HOST              - 默认 127.0.0.1
        MYSQL_PORT              - 默认 3306
        MYSQL_DATABASE          - 默认 xhs_db
        MYSQL_USER              - 默认 root
        MYSQL_PASSWORD          - 默认 空字符串（本地开发无密码）
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

try:
    from pydantic import Field, field_validator, model_validator
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError as e:  # pragma: no cover - 依赖缺失时给出清晰报错
    raise SystemExit(
        "缺少依赖：pydantic>=2.6, pydantic-settings>=2。请先运行：pip install -r requirements.txt"
    ) from e


_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """团队共享的配置类。成员 B 的 FastAPI app 可直接导入复用，无需再定义一套。"""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    DATABASE_URL: Optional[str] = None
    MYSQL_HOST: str = "127.0.0.1"
    MYSQL_PORT: int = 3306
    MYSQL_DATABASE: str = "xhs_db"
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = ""

    UPLOAD_DIR: str = "./uploads"
    APP_ENV: str = Field(default="development", alias="FLASK_ENV")

    @model_validator(mode="after")
    def _build_database_url(self) -> "Settings":
        if self.DATABASE_URL:
            return self
        host = self.MYSQL_HOST or "127.0.0.1"
        port = int(self.MYSQL_PORT or 3306)
        db = self.MYSQL_DATABASE or "xhs_db"
        user = self.MYSQL_USER or "root"
        pwd = self.MYSQL_PASSWORD or ""
        if pwd:
            self.DATABASE_URL = f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{db}?charset=utf8mb4"
        else:
            self.DATABASE_URL = f"mysql+pymysql://{user}@{host}:{port}/{db}?charset=utf8mb4"
        return self


_settings_singleton: Optional[Settings] = None


def get_settings() -> Settings:
    """单例 getter。避免每次调用都去读 .env / 环境变量。"""
    global _settings_singleton
    if _settings_singleton is None:
        _settings_singleton = Settings()
    return _settings_singleton


_USER_PASSWORD_RE = re.compile(r"^(?P<scheme>[a-zA-Z][\w\+\.\-]*)://(?P<user>[^:@/\s]+):(?P<pwd>[^@/\s]+)@")


def mask_database_url(url: str) -> str:
    """对数据库 URL 中的密码部分做脱敏，用于任何打印 / 日志 / 异常信息。

    mysql+pymysql://u:secret@h:3306/db  ->  mysql+pymysql://u:***@h:3306/db
    sqlite:///./xhs.db                 ->  sqlite:///./xhs.db (不变)
    """
    if not url:
        return ""
    m = _USER_PASSWORD_RE.match(url)
    if not m:
        return url
    scheme, user = m.group("scheme"), m.group("user")
    head_end = m.end()
    return f"{scheme}://{user}:***@{url[head_end:]}"


def parse_mysql_url(url: str) -> Optional[tuple[str, str, str, int, str]]:
    """从 mysql+pymysql://... URL 解析出 (user, password, host, port, database)。

    非 mysql 协议或解析失败返回 None。
    """
    try:
        if not url.startswith("mysql"):
            return None
        without_scheme = url.split("://", 1)[1]
        auth, host_part = without_scheme.rsplit("@", 1)
        if ":" in auth:
            user, password = auth.split(":", 1)
        else:
            user, password = auth, ""
        hostport, database = host_part.split("/", 1)
        if "?" in database:
            database = database.split("?", 1)[0]
        if ":" in hostport:
            host, port_str = hostport.split(":", 1)
            port = int(port_str)
        else:
            host, port = hostport, 3306
        return user, password, host, port, database
    except Exception:
        return None


__all__ = [
    "Settings",
    "get_settings",
    "mask_database_url",
    "parse_mysql_url",
]
