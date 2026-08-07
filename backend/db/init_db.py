"""幂等数据库初始化脚本（单一共享 Settings，URL.create() 构造连接串）。

用法：
    python backend/db/init_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.core.config import Settings, ensure_uploads_dir, get_settings, mask_database_url
from backend.db import init_database, close_global_engine_session
from sqlalchemy.orm import close_all_sessions


def main() -> None:
    settings: Settings = get_settings()
    masked = mask_database_url(settings.DATABASE_URL or "")
    ensure_uploads_dir(settings)
    engine, session_factory = init_database(settings)
    try:
        db_name = settings.MYSQL_DATABASE if (settings.DATABASE_URL or "").startswith("mysql") else ""
        print(f"[OK] 数据库 {db_name} 已就绪")
        print(f"[OK] 表 generation_records 已就绪（DATABASE_URL={masked}）")
        print("[DONE] 数据库初始化完成，可重复执行。")
    finally:
        try:
            close_all_sessions()
        except Exception:
            pass
        try:
            engine.dispose()
        except Exception:
            pass
        close_global_engine_session()


if __name__ == "__main__":
    main()
