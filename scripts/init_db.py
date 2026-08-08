#!/usr/bin/env python3
"""数据库初始化脚本：可重复执行，确保数据库和表存在。"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()


def init_database_mysql():
    """通过 PyMySQL 直接执行建库建表 SQL（可重复执行）。"""
    try:
        import pymysql
    except ImportError:
        print("[ERROR] 请先安装依赖: pip install pymysql")
        sys.exit(1)

    db_url = os.getenv("DATABASE_URL", "")
    parsed = _parse_mysql_url(db_url)
    if not parsed:
        print("[ERROR] DATABASE_URL 格式错误，预期: mysql+pymysql://user:pwd@host:port/dbname")
        sys.exit(1)

    user, password, host, port, database = parsed

    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cursor:
            create_db_sql = f"""
            CREATE DATABASE IF NOT EXISTS `{database}`
            DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
            """
            cursor.execute(create_db_sql)
            print(f"[OK] 数据库 {database} 已就绪")

        conn.select_db(database)

        create_table_sql = """
        CREATE TABLE IF NOT EXISTS generation_records (
            id INT AUTO_INCREMENT PRIMARY KEY,
            task_id VARCHAR(64) NOT NULL UNIQUE,
            status VARCHAR(16) NOT NULL DEFAULT 'pending',

            image_path VARCHAR(512) NULL,
            image_description TEXT NULL,

            user_input TEXT NULL,

            title VARCHAR(100) NULL,
            content TEXT NULL,
            tags JSON NULL,

            error_code VARCHAR(64) NULL,
            error_message TEXT NULL,

            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

            INDEX idx_task_id (task_id),
            INDEX idx_status (status),
            INDEX idx_created_at (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
        with conn.cursor() as cursor:
            cursor.execute(create_table_sql)
            print("[OK] 表 generation_records 已就绪")

        conn.commit()
        print("[DONE] 数据库初始化完成，可重复执行。")
    finally:
        conn.close()


def init_database_sqlite(app):
    """对于 sqlite（默认开发环境），使用 SQLAlchemy 建表。"""
    from app import db
    with app.app_context():
        db.create_all()
        print("[OK] SQLite 表已就绪")


def _parse_mysql_url(url: str):
    """mysql+pymysql://user:pwd@host:port/dbname -> (user, pwd, host, port, db)"""
    try:
        url = url.replace("mysql+pymysql://", "")
        auth_part, host_part = url.rsplit("@", 1)
        user, password = auth_part.split(":", 1)
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


if __name__ == "__main__":
    db_url = os.getenv("DATABASE_URL", "")
    if db_url.startswith("mysql"):
        init_database_mysql()
    else:
        from app import create_app
        app = create_app()
        init_database_sqlite(app)
