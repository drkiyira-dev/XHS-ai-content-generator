#!/usr/bin/env python3
"""Flask 启动入口。"""
from app import create_app
from app.config import Config

app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=Config.FLASK_PORT, debug=Config.FLASK_ENV == "development")
