"""Image-to-copy generation endpoint backed by Qwen3-VL."""

from datetime import UTC, datetime
from typing import Annotated, cast
from uuid import uuid4

from fastapi import APIRouter, File, Form, Request, UploadFile
from typing_extensions import TypedDict

from backend.api.errors import APIError, ErrorResponse
from backend.core.config import Settings
from backend.services.image import (
    delete_processed_image,
    delete_validated_image,
    preprocess_validated_image,
    validate_uploaded_image,
)
from backend.services.model import GenerationModelService


router = APIRouter(prefix="/generations", tags=["generations"])


class GenerationResponse(TypedDict):
    """Frozen response contract used before C supplies shared schemas."""

    generation_id: str
    image_summary: str
    title: str
    body: str
    tags: list[str]
    created_at: datetime


@router.post(
    "",
    responses={
        400: {"model": ErrorResponse, "description": "Image is missing"},
        413: {"model": ErrorResponse, "description": "Image is too large"},
        415: {"model": ErrorResponse, "description": "Image type is unsupported"},
        422: {"model": ErrorResponse, "description": "Image cannot be decoded"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        502: {"model": ErrorResponse, "description": "Model service failure"},
        504: {"model": ErrorResponse, "description": "Model service timeout"},
    },
)
async def create_generation(
    request: Request,
    image: Annotated[
        UploadFile,
        File(
            description="One JPG, JPEG, PNG, or WebP image to analyze",
            json_schema_extra={"format": "binary"},
        ),
    ],
    product_name: Annotated[str | None, Form()] = None,
    target_audience: Annotated[str | None, Form()] = None,
    tone: Annotated[str | None, Form()] = None,
) -> GenerationResponse:
    """Validate one image and return Qwen-generated structured copy."""
    image_parts = (await request.form()).getlist("image")
    if len(image_parts) != 1:
        raise APIError(
            code="UNSUPPORTED_IMAGE_TYPE",
            message="当前接口只支持上传一张图片。",
            status_code=415,
        )

    settings: Settings = request.app.state.settings
    model_service = cast(
        GenerationModelService,
        request.app.state.model_service,
    )
    validated_image = await validate_uploaded_image(image, settings)
    processed_image = None

    try:
        processed_image = await preprocess_validated_image(validated_image, settings)
        generated_copy = await model_service.generate(
            processed_image,
            product_name=product_name,
            target_audience=target_audience,
            tone=tone,
        )

        return {
            "generation_id": str(uuid4()),
            "image_summary": generated_copy.image_summary,
            "title": generated_copy.title,
            "body": generated_copy.body,
            "tags": list(generated_copy.tags),
            "created_at": datetime.now(UTC),
        }
    finally:
        try:
            if processed_image is not None:
                await delete_processed_image(processed_image)
        finally:
            try:
                await delete_validated_image(validated_image)
            finally:
                product_name = None
                target_audience = None
                tone = None
