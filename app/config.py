import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./xhs.db")
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    MODEL_NAME = os.getenv("MODEL_NAME", "qwen-vl-plus")
    UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
    FLASK_ENV = os.getenv("FLASK_ENV", "development")
    FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
    SQLALCHEMY_TRACK_MODIFICATIONS = False
