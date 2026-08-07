"""数据库初始化 & 验证脚本（FastAPI + 纯 SQLAlchemy 2.x）。

幂等（可重复执行，不破坏已有数据）。

用法：
    python backend/db/init_db.py

支持：
    - 若 DATABASE_URL 是 mysql+pymysql://...，先用 PyMySQL 建库，再 SQLAlchemy create_all 建表
    - 其他情况（含 SQLite），直接用 SQLAlchemy create_all 建表

打印的 DATABASE_URL 都会做 mask_database_url 脱敏（密码变成 ***），不会泄露。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.db import init_database
from backend.db.config import Settings, mask_database_url


def _ensure_uploads_dir(settings: Settings) -> None:
    try:
        os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    except Exception:
        pass


def main() -> None:
    settings = Settings()
    masked = mask_database_url(settings.DATABASE_URL or "")
    _ensure_uploads_dir(settings)
    engine, session_factory = init_database(settings)
    try:
        print(f"[OK] 数据库 {settings.MYSQL_DATABASE if (settings.DATABASE_URL or '').startswith('mysql') else ''} 已就绪")
        print(f"[OK] 表 generation_records 已就绪（DATABASE_URL={masked}）")
        print("[DONE] 数据库初始化完成，可重复执行。")
    finally:
        try:
            from sqlalchemy.orm import close_all_sessions
            close_all_sessions()
        except Exception:
            pass
        try:
            engine.dispose()
        except Exception:
            pass


if __name__ == "__main__":
    main()
