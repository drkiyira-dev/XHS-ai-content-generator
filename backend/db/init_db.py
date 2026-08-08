"""幂等数据库初始化脚本（独立 DatabaseConfig，不与 B 的 settings 冲突）。

用法：
    python backend/db/init_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.db.config import (
    DatabaseConfig,
    ensure_uploads_dir,
    get_database_config,
    mask_database_url,
)
from backend.db import init_database, close_global_engine_session
from backend.db.config import parse_mysql_url
from sqlalchemy.orm import close_all_sessions


def main() -> None:
    cfg: DatabaseConfig = get_database_config()
    masked = mask_database_url(cfg.DATABASE_URL or "")
    ensure_uploads_dir()
    engine, session_factory = init_database(cfg)
    try:
        db_name = ""
        if (cfg.DATABASE_URL or "").startswith("mysql"):
            parsed = parse_mysql_url(cfg.DATABASE_URL or "")
            db_name = parsed[4] if parsed else getattr(cfg, "MYSQL_DATABASE", "") or ""
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
