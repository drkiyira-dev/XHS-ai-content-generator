"""Flask 配置：所有敏感配置均来自环境变量。

支持的环境变量：
    DATABASE_URL          - 首选，完整数据库连接串（MySQL/SQLite/PostgreSQL）
    MYSQL_HOST            - 当 DATABASE_URL 未设置时生效，默认 127.0.0.1
    MYSQL_PORT            - 默认 3306
    MYSQL_DATABASE        - 默认 xhs_db
    MYSQL_USER            - 默认 root
    MYSQL_PASSWORD        - 默认 空（本地开发）
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _build_mysql_url() -> str:
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = int(os.getenv("MYSQL_PORT", "3306"))
    db_name = os.getenv("MYSQL_DATABASE", "xhs_db")
    user = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PASSWORD", "")
    if password:
        return f"mysql+pymysql://{user}:{password}@{host}:{port}/{db_name}?charset=utf8mb4"
    return f"mysql+pymysql://{user}@{host}:{port}/{db_name}?charset=utf8mb4"


class Config:
    DATABASE_URL = os.getenv("DATABASE_URL") or _build_mysql_url()
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
    FLASK_ENV = os.getenv("FLASK_ENV", "development")
    FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
