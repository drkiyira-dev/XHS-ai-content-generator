"""Image-to-copy generation endpoint backed by Qwen3-VL."""

import logging
from datetime import UTC, datetime
from typing import Annotated, cast
from uuid import uuid4

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile
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
from backend.services.persistence import (
    FailedGeneration,
    GenerationPersistence,
    GenerationPersistenceError,
    PendingGeneration,
    SuccessfulGeneration,
)


router = APIRouter(prefix="/generations", tags=["generations"])
logger = logging.getLogger(__name__)


class GenerationResponse(TypedDict):
    """Frozen response contract used before C supplies shared schemas."""

    generation_id: str
    image_summary: str
    title: str
    body: str
    tags: list[str]
    created_at: datetime


class GenerationHistoryResponse(TypedDict):
    """Recent successful generations for the local single-user history page."""

    items: list[GenerationResponse]
    count: int


@router.get(
    "",
    responses={
        500: {"model": ErrorResponse, "description": "Database read failure"},
    },
)
async def list_generations(
    request: Request,
    response: Response,
    limit: Annotated[
        int,
        Query(ge=1, le=50, description="Maximum successful records to return"),
    ] = 20,
) -> GenerationHistoryResponse:
    """Return recent successful generations without exposing local image paths."""
    generation_persistence = cast(
        GenerationPersistence,
        request.app.state.generation_persistence,
    )
    try:
        records = await generation_persistence.list_successful(limit=limit)
    except GenerationPersistenceError as error:
        logger.error(
            "Generation persistence failed stage=list_successful type=%s",
            type(error).__name__,
        )
        raise APIError(
            code="DATABASE_ERROR",
            message="历史记录暂时无法读取，请稍后重试。",
            status_code=500,
            retryable=True,
        ) from None

    items: list[GenerationResponse] = [
        {
            "generation_id": record.generation_id,
            "image_summary": record.image_summary,
            "title": record.title,
            "body": record.body,
            "tags": list(record.tags),
            "created_at": record.created_at,
        }
        for record in records
    ]
    response.headers["Cache-Control"] = "no-store"
    return {"items": items, "count": len(items)}


@router.post(
    "",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Image is missing or a form field is too large",
        },
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
    generation_persistence = cast(
        GenerationPersistence,
        request.app.state.generation_persistence,
    )
    validated_image = await validate_uploaded_image(image, settings)
    processed_image = None

    try:
        processed_image = await preprocess_validated_image(validated_image, settings)
        generation_id = str(uuid4())
        created_at = datetime.now(UTC)
        try:
            await generation_persistence.create_pending(
                PendingGeneration(
                    generation_id=generation_id,
                    created_at=created_at,
                )
            )
        except GenerationPersistenceError as error:
            _log_persistence_failure(
                stage="create_pending",
                generation_id=generation_id,
                error=error,
            )
            raise _database_error() from None

        try:
            generated_copy = await model_service.generate(
                processed_image,
                product_name=product_name,
                target_audience=target_audience,
                tone=tone,
            )
        except APIError as error:
            await _mark_failed_best_effort(
                generation_persistence,
                FailedGeneration(
                    generation_id=generation_id,
                    error_code=error.code,
                    failed_at=datetime.now(UTC),
                )
            )
            raise
        except Exception:
            await _mark_failed_best_effort(
                generation_persistence,
                FailedGeneration(
                    generation_id=generation_id,
                    error_code="INTERNAL_ERROR",
                    failed_at=datetime.now(UTC),
                )
            )
            raise

        try:
            await generation_persistence.mark_success(
                SuccessfulGeneration(
                    generation_id=generation_id,
                    image_summary=generated_copy.image_summary,
                    title=generated_copy.title,
                    body=generated_copy.body,
                    tags=generated_copy.tags,
                )
            )
        except GenerationPersistenceError as error:
            _log_persistence_failure(
                stage="mark_success",
                generation_id=generation_id,
                error=error,
            )
            raise _database_error() from None

        return {
            "generation_id": generation_id,
            "image_summary": generated_copy.image_summary,
            "title": generated_copy.title,
            "body": generated_copy.body,
            "tags": list(generated_copy.tags),
            "created_at": created_at,
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


async def _mark_failed_best_effort(
    persistence: GenerationPersistence,
    record: FailedGeneration,
) -> None:
    """Preserve the primary model failure if failure persistence also fails."""
    try:
        await persistence.mark_failed(record)
    except Exception as error:
        logger.error(
            "Generation persistence failed stage=mark_failed "
            "type=%s generation_id=%s",
            type(error).__name__,
            record.generation_id,
        )


def _log_persistence_failure(
    *,
    stage: str,
    generation_id: str,
    error: GenerationPersistenceError,
) -> None:
    """Log fixed operational metadata without database error contents."""
    logger.error(
        "Generation persistence failed stage=%s type=%s generation_id=%s",
        stage,
        type(error).__name__,
        generation_id,
    )


def _database_error() -> APIError:
    """Build the frozen public response for a known persistence outage."""
    return APIError(
        code="DATABASE_ERROR",
        message="生成结果暂时无法保存，请稍后重试。",
        status_code=500,
        retryable=True,
    )
