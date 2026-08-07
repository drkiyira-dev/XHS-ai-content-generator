"""数据库初始化 & 验证脚本。

支持 MySQL / SQLite：
    - 若设置了 DATABASE_URL 且是 mysql+pymysql:// 前缀，则用 PyMySQL 先建库再 SQLAlchemy 建表
    - 否则按 DATABASE_URL（默认 sqlite:///./xhs.db）用 SQLAlchemy create_all() 建表
"""
import os
import sys


def init_mysql_database(database_url: str) -> None:
    """用 PyMySQL 直接建库（保证 create_all 能连上库）。"""
    try:
        import pymysql  # noqa: F401
    except ImportError:
        print("[WARN] PyMySQL 未安装，跳过 MySQL 建库步骤")
        return

    parsed = _parse_mysql_url(database_url)
    if not parsed:
        print("[WARN] DATABASE_URL 不符合 mysql+pymysql:// 格式，跳过 MySQL 建库步骤")
        return
    user, password, host, port, database = parsed

    import pymysql
    conn = pymysql.connect(host=host, port=port, user=user, password=password or "", charset="utf8mb4")
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
            )
        print(f"[OK] 数据库 {database} 已就绪")
    finally:
        conn.close()


def _parse_mysql_url(url: str):
    try:
        url = url.replace("mysql+pymysql://", "")
        auth, host_part = url.rsplit("@", 1)
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


def init_with_flask():
    """在 Flask app context 中 create_all。"""
    # 轻量引入，避免循环依赖
    from flask import Flask
    from backend.db.config import Config
    from backend.db import init_database

    if Config.DATABASE_URL.startswith("mysql"):
        init_mysql_database(Config.DATABASE_URL)

    app = Flask(__name__)
    app.config.from_object(Config)
    init_database(app)
    print(f"[OK] 表 generation_records 已就绪（DATABASE_URL={Config.DATABASE_URL}）")


if __name__ == "__main__":
    os.makedirs(os.path.join(os.path.dirname(__file__), "..", "..", "uploads"), exist_ok=True)
    init_with_flask()
    print("[DONE] 数据库初始化完成，可重复执行。")
