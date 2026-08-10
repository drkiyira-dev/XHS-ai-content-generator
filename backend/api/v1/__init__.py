"""Version 1 API router."""

from fastapi import APIRouter

from backend.api.v1.generations import router as generations_router


router = APIRouter()
router.include_router(generations_router)
