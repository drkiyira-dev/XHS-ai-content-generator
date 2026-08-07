"""ASGI entry point used by Uvicorn."""

from backend.app import create_app
from backend.core.config import get_settings


app = create_app(get_settings())
