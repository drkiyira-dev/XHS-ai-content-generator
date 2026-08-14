"""Image-to-copy generation endpoint backed by Qwen3-VL."""

import logging
from datetime import UTC, datetime
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import ValidationError
from starlette.datastructures import FormData, UploadFile
from typing_extensions import TypedDict

from backend.api.auth_dependencies import (
    CsrfHeader,
    auth_unavailable_error,
    get_auth_service,
    require_csrf_request,
    require_current_user,
)
from backend.api.errors import APIError, ErrorResponse
from backend.core.config import Settings
from backend.services.auth import AuthenticatedUser
from backend.services.image import (
    HistoryPreviewError,
    PREVIEW_MAX_BYTES,
    create_history_image_preview,
    delete_processed_image,
    delete_validated_image,
    preprocess_validated_image,
    validate_uploaded_image,
)
from backend.services.model import GeneratedCopy, GenerationModelService
from backend.services.model.content_risk import (
    CONTENT_RISK_RULE_VERSION,
    ContentRiskFinding,
    scan_content_risks,
)
from backend.services.model.enhancement import (
    ContentCategory,
    EmojiLevel,
    enhance_generated_copy,
    infer_content_category,
)
from backend.services.model.safety import (
    find_strict_unsupported_claim_rule,
    find_unsupported_claim_rule,
)
from backend.services.persistence import (
    FailedGeneration,
    GenerationPersistence,
    GenerationPersistenceError,
    PendingGeneration,
    StoredImagePreview,
    SuccessfulGeneration,
)
from backend.schemas import RiskAssessmentSnapshot, RiskFindingSnapshot


router = APIRouter(prefix="/generations", tags=["generations"])
logger = logging.getLogger(__name__)
FORM_TEXT_LIMIT_BYTES = 1024 * 1024
HISTORY_CACHE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Vary": "Cookie",
}
GENERATION_FORM_FIELDS = frozenset(
    {
        "image",
        "product_name",
        "target_audience",
        "tone",
        "emoji_level",
        "related_tags",
    }
)
GENERATION_MULTIPART_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["image"],
                    "properties": {
                        "image": {
                            "type": "string",
                            "format": "binary",
                            "description": (
                                "One JPG, JPEG, PNG, WebP, or single-frame "
                                "HEIC/HEIF image to analyze"
                            ),
                        },
                        "product_name": {"type": "string"},
                        "target_audience": {"type": "string"},
                        "tone": {"type": "string"},
                        "emoji_level": {
                            "type": "string",
                            "enum": ["off", "light", "expressive"],
                            "default": "off",
                            "description": "Optional deterministic emoji style",
                        },
                        "related_tags": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Append bounded local related tags; this is not "
                                "a live popularity ranking"
                            ),
                        },
                    },
                }
            }
        },
    }
}


async def _resolve_generation_user(
    request: Request,
) -> AuthenticatedUser | None:
    """Return a cookie identity, or the explicit legacy NULL partition."""
    settings: Settings = request.app.state.settings
    if not settings.auth_enabled:
        return None

    user = await require_current_user(request, get_auth_service(request))
    if (
        isinstance(user.user_id, bool)
        or not isinstance(user.user_id, int)
        or user.user_id <= 0
    ):
        raise auth_unavailable_error()
    return user


async def _resolve_generation_write_user(
    request: Request,
    csrf_marker: CsrfHeader = None,
) -> AuthenticatedUser | None:
    """Authenticate and check CSRF before the endpoint reads multipart bytes."""
    user = await _resolve_generation_user(request)
    if user is not None:
        require_csrf_request(request, csrf_marker)
    return user


GenerationUser = Annotated[
    AuthenticatedUser | None,
    Depends(_resolve_generation_user),
]
GenerationWriteUser = Annotated[
    AuthenticatedUser | None,
    Depends(_resolve_generation_write_user),
]


class GenerationResponse(TypedDict):
    """Frozen response contract used before C supplies shared schemas."""

    generation_id: str
    image_summary: str
    title: str
    body: str
    tags: list[str]
    created_at: datetime
    risk_assessment: "RiskAssessmentResponse"


class RiskFindingResponse(TypedDict):
    """One bounded advisory hint; never a platform-review verdict."""

    code: str
    severity: Literal["low", "medium", "high"]
    field: Literal["title", "body", "tags"]
    reason: str
    suggestion: str


class RiskAssessmentResponse(TypedDict):
    """Versioned pre-publication hints for human review."""

    rule_version: str
    findings: list[RiskFindingResponse]


class GenerationHistoryItemResponse(GenerationResponse):
    """One history item without exposing private preview bytes or DB fields."""

    has_image_preview: bool
    image_preview_url: str | None


class GenerationHistoryResponse(TypedDict):
    """Recent successful generations for the local single-user history page."""

    items: list[GenerationHistoryItemResponse]
    count: int


@router.get(
    "",
    responses={
        401: {"model": ErrorResponse, "description": "Login required"},
        500: {"model": ErrorResponse, "description": "Database read failure"},
        503: {"model": ErrorResponse, "description": "Account service unavailable"},
    },
)
async def list_generations(
    request: Request,
    response: Response,
    current_user: GenerationUser,
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
        records = await generation_persistence.list_successful(
            user_id=_user_id(current_user),
            limit=limit,
        )
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

    items: list[GenerationHistoryItemResponse] = []
    for record in records:
        try:
            stored_copy = GeneratedCopy(
                image_summary=record.image_summary,
                title=record.title,
                body=record.body,
                tags=record.tags,
            )
            stored_risk_assessment = _history_risk_assessment(
                record.risk_assessment
            )
        except (TypeError, ValueError, ValidationError) as error:
            logger.error(
                "Generation persistence returned invalid history data "
                "type=%s generation_id=%s",
                type(error).__name__,
                record.generation_id,
            )
            raise _history_database_error() from None
        items.append(
            {
                "generation_id": record.generation_id,
                "image_summary": stored_copy.image_summary,
                "title": stored_copy.title,
                "body": stored_copy.body,
                "tags": list(stored_copy.tags),
                "created_at": record.created_at,
                "risk_assessment": _risk_assessment_response(
                    stored_risk_assessment
                ),
                "has_image_preview": record.has_image_preview,
                "image_preview_url": (
                    _image_preview_url(record.generation_id)
                    if record.has_image_preview
                    else None
                ),
            }
        )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Vary"] = "Cookie"
    return {"items": items, "count": len(items)}


@router.get(
    "/{generation_id}/image-preview",
    responses={
        401: {"model": ErrorResponse, "description": "Login required"},
        404: {"model": ErrorResponse, "description": "Preview not found"},
        500: {"model": ErrorResponse, "description": "Database read failure"},
        503: {"model": ErrorResponse, "description": "Account service unavailable"},
    },
)
async def get_generation_image_preview(
    request: Request,
    generation_id: UUID,
    current_user: GenerationUser,
) -> Response:
    """Return bounded preview bytes only from the current owner partition."""
    canonical_id = str(generation_id)
    user_id = _user_id(current_user)
    generation_persistence = cast(
        GenerationPersistence,
        request.app.state.generation_persistence,
    )
    try:
        preview = await generation_persistence.get_image_preview(
            generation_id=canonical_id,
            user_id=user_id,
        )
    except GenerationPersistenceError as error:
        _log_persistence_failure(
            stage="get_image_preview",
            generation_id=canonical_id,
            error=error,
        )
        raise _history_database_error() from None

    if preview is None:
        raise _generation_not_found_error()
    if not _valid_stored_preview(
        preview,
        generation_id=canonical_id,
        user_id=user_id,
    ):
        logger.error(
            "Generation persistence returned invalid preview "
            "stage=get_image_preview generation_id=%s",
            canonical_id,
        )
        raise _history_database_error() from None

    return Response(
        content=preview.content,
        media_type=preview.media_type,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Vary": "Cookie",
        },
    )


@router.delete(
    "/{generation_id}",
    status_code=204,
    responses={
        401: {"model": ErrorResponse, "description": "Login required"},
        403: {"model": ErrorResponse, "description": "Request origin rejected"},
        404: {"model": ErrorResponse, "description": "Generation not found"},
        500: {"model": ErrorResponse, "description": "Database write failure"},
        503: {"model": ErrorResponse, "description": "Account service unavailable"},
    },
)
async def delete_generation(
    request: Request,
    generation_id: UUID,
    current_user: GenerationWriteUser,
) -> Response:
    """Soft-delete one success without revealing other owner partitions."""
    canonical_id = str(generation_id)
    generation_persistence = cast(
        GenerationPersistence,
        request.app.state.generation_persistence,
    )
    try:
        deleted = await generation_persistence.delete_successful(
            generation_id=canonical_id,
            user_id=_user_id(current_user),
            deleted_at=datetime.now(UTC),
        )
    except GenerationPersistenceError as error:
        _log_persistence_failure(
            stage="delete_successful",
            generation_id=canonical_id,
            error=error,
        )
        raise _history_database_error() from None

    if deleted is None:
        raise _generation_not_found_error()

    return Response(
        status_code=204,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Vary": "Cookie",
        },
    )


@router.post(
    "",
    openapi_extra=GENERATION_MULTIPART_OPENAPI,
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Image is missing or a form field is too large",
        },
        413: {"model": ErrorResponse, "description": "Image is too large"},
        415: {"model": ErrorResponse, "description": "Image type is unsupported"},
        422: {"model": ErrorResponse, "description": "Image cannot be decoded"},
        401: {"model": ErrorResponse, "description": "Login required"},
        403: {"model": ErrorResponse, "description": "Request origin rejected"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        502: {"model": ErrorResponse, "description": "Model service failure"},
        503: {"model": ErrorResponse, "description": "Account service unavailable"},
        504: {"model": ErrorResponse, "description": "Model service timeout"},
    },
)
async def create_generation(
    request: Request,
    current_user: GenerationWriteUser,
) -> GenerationResponse:
    """Validate one image and return Qwen-generated structured copy."""
    settings: Settings = request.app.state.settings
    model_service = cast(
        GenerationModelService,
        request.app.state.model_service,
    )
    generation_persistence = cast(
        GenerationPersistence,
        request.app.state.generation_persistence,
    )
    async with request.form(
        max_files=4,
        max_fields=6,
        max_part_size=FORM_TEXT_LIMIT_BYTES,
    ) as form:
        (
            image,
            product_name,
            target_audience,
            tone,
            emoji_level,
            include_related_tags,
        ) = _parse_generation_form(form)
        validated_image = await validate_uploaded_image(image, settings)

    user_id = _user_id(current_user)
    processed_image = None

    try:
        processed_image = await preprocess_validated_image(validated_image, settings)
        generation_id = str(uuid4())
        created_at = datetime.now(UTC)
        try:
            await generation_persistence.create_pending(
                PendingGeneration(
                    generation_id=generation_id,
                    user_id=user_id,
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
            generated_copy = GeneratedCopy.model_validate(
                await model_service.generate(
                    processed_image,
                    product_name=product_name,
                    target_audience=target_audience,
                    tone=tone,
                )
            )
        except ValidationError:
            error = APIError(
                code="MODEL_OUTPUT_INVALID",
                message="模型返回的内容格式无效，请重试。",
                status_code=502,
                retryable=True,
            )
            await _mark_failed_best_effort(
                generation_persistence,
                FailedGeneration(
                    generation_id=generation_id,
                    user_id=user_id,
                    error_code=error.code,
                    failed_at=datetime.now(UTC),
                ),
            )
            raise error from None
        except APIError as error:
            await _mark_failed_best_effort(
                generation_persistence,
                FailedGeneration(
                    generation_id=generation_id,
                    user_id=user_id,
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
                    user_id=user_id,
                    error_code="INTERNAL_ERROR",
                    failed_at=datetime.now(UTC),
                )
            )
            raise

        generated_copy = _apply_optional_enhancement(
            generated_copy,
            emoji_level=emoji_level,
            include_related_tags=include_related_tags,
        )
        risk_assessment = _risk_assessment(generated_copy)

        image_preview = None
        try:
            image_preview = await create_history_image_preview(processed_image)
        except HistoryPreviewError as error:
            logger.warning(
                "History image preview unavailable type=%s generation_id=%s",
                type(error).__name__,
                generation_id,
            )

        try:
            await generation_persistence.mark_success(
                SuccessfulGeneration(
                    generation_id=generation_id,
                    user_id=user_id,
                    image_summary=generated_copy.image_summary,
                    title=generated_copy.title,
                    body=generated_copy.body,
                    tags=generated_copy.tags,
                    risk_assessment=risk_assessment,
                    image_preview=(
                        image_preview.content if image_preview is not None else None
                    ),
                    image_preview_media_type=(
                        image_preview.media_type if image_preview is not None else None
                    ),
                )
            )
        except GenerationPersistenceError as error:
            _log_persistence_failure(
                stage="mark_success",
                generation_id=generation_id,
                error=error,
            )
            raise _database_error() from None
        finally:
            image_preview = None

        return {
            "generation_id": generation_id,
            "image_summary": generated_copy.image_summary,
            "title": generated_copy.title,
            "body": generated_copy.body,
            "tags": list(generated_copy.tags),
            "created_at": created_at,
            "risk_assessment": _risk_assessment_response(risk_assessment),
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


def _parse_generation_form(
    form: FormData,
) -> tuple[
    UploadFile,
    str | None,
    str | None,
    str | None,
    EmojiLevel,
    bool,
]:
    """Accept only the documented multipart fields with no duplicates."""
    if any(name not in GENERATION_FORM_FIELDS for name, _value in form.multi_items()):
        raise _invalid_form_error()

    image_parts = form.getlist("image")
    if not image_parts:
        raise APIError(
            code="IMAGE_REQUIRED",
            message="请上传一张图片。",
            status_code=400,
        )
    if len(image_parts) != 1:
        raise APIError(
            code="UNSUPPORTED_IMAGE_TYPE",
            message="当前接口只支持上传一张图片。",
            status_code=415,
        )
    if not isinstance(image_parts[0], UploadFile):
        raise APIError(
            code="UNSUPPORTED_IMAGE_TYPE",
            message="图片必须通过文件上传控件提交。",
            status_code=415,
        )

    values: list[str | None] = []
    for field_name in ("product_name", "target_audience", "tone"):
        field_parts = form.getlist(field_name)
        if not field_parts:
            values.append(None)
            continue
        if len(field_parts) != 1 or not isinstance(field_parts[0], str):
            raise _invalid_form_error()
        values.append(field_parts[0])

    emoji_level = _parse_emoji_level(form)
    include_related_tags = _parse_related_tags(form)
    return (
        image_parts[0],
        values[0],
        values[1],
        values[2],
        emoji_level,
        include_related_tags,
    )


def _parse_emoji_level(form: FormData) -> EmojiLevel:
    parts = form.getlist("emoji_level")
    if not parts:
        return EmojiLevel.OFF
    if len(parts) != 1 or not isinstance(parts[0], str):
        raise _invalid_form_error()
    try:
        return EmojiLevel(parts[0])
    except ValueError:
        raise _invalid_form_error() from None


def _parse_related_tags(form: FormData) -> bool:
    parts = form.getlist("related_tags")
    if not parts:
        return False
    if len(parts) != 1 or not isinstance(parts[0], str):
        raise _invalid_form_error()
    if parts[0] == "true":
        return True
    if parts[0] == "false":
        return False
    raise _invalid_form_error()


def _apply_optional_enhancement(
    copy: GeneratedCopy,
    *,
    emoji_level: EmojiLevel,
    include_related_tags: bool,
) -> GeneratedCopy:
    """Apply deterministic decoration, then re-run the existing fact guard."""
    if emoji_level is EmojiLevel.OFF and not include_related_tags:
        return copy

    try:
        category: ContentCategory = infer_content_category(copy)
        enhanced = enhance_generated_copy(
            copy,
            emoji_level=emoji_level,
            category=category,
            include_related_tags=include_related_tags,
        )
        enhanced = GeneratedCopy.model_validate(enhanced)
        violation_rule = find_unsupported_claim_rule(enhanced, ocr_text=None)
        if violation_rule is None:
            violation_rule = find_strict_unsupported_claim_rule(
                enhanced,
                ocr_text=None,
            )
    except Exception as error:
        logger.error(
            "Optional copy enhancement failed type=%s; using validated model copy",
            type(error).__name__,
        )
        return copy

    if violation_rule is not None:
        logger.warning(
            "Optional copy enhancement rejected rule=%s; using validated model copy",
            violation_rule,
        )
        return copy
    return enhanced


def _risk_assessment(copy: GeneratedCopy) -> RiskAssessmentSnapshot:
    """Build the immutable generation-time snapshot without matched text."""
    public_findings: list[RiskFindingSnapshot] = []
    seen: set[tuple[str, str]] = set()
    try:
        findings = scan_content_risks(copy)
        for finding in findings:
            field = _public_risk_field(finding)
            if field is None or (finding.code, field) in seen:
                continue
            seen.add((finding.code, field))
            public_findings.append(
                RiskFindingSnapshot(
                    code=finding.code,
                    severity=finding.severity,
                    field=field,
                    reason=finding.reason,
                    suggestion=finding.suggestion,
                )
            )
        return RiskAssessmentSnapshot(
            rule_version=CONTENT_RISK_RULE_VERSION,
            findings=tuple(public_findings),
        )
    except Exception as error:
        logger.error(
            "Content risk scan unavailable type=%s",
            type(error).__name__,
        )
        return RiskAssessmentSnapshot(
            rule_version=CONTENT_RISK_RULE_VERSION,
            findings=(
                RiskFindingSnapshot(
                    code="risk_scan_unavailable",
                    severity="high",
                    field="body",
                    reason="本地发布风险扫描暂时不可用，当前结果尚未完成该项检查。",
                    suggestion="发布前请完整人工核对正文与话题标签，稍后可重新生成以再次检查。",
                ),
            ),
        )


def _history_risk_assessment(
    snapshot: RiskAssessmentSnapshot | None,
) -> RiskAssessmentSnapshot:
    """Return a stored snapshot, or an explicit warning for legacy NULL rows."""
    if snapshot is not None:
        return RiskAssessmentSnapshot.model_validate(snapshot)
    return RiskAssessmentSnapshot(
        rule_version="legacy-no-risk-snapshot",
        findings=(
            RiskFindingSnapshot(
                code="legacy_risk_snapshot_unavailable",
                severity="high",
                field="body",
                reason="该历史记录生成时未保存发布风险快照，无法还原当时的检查结果。",
                suggestion="请重新人工核对标题、正文与话题标签，不要将当前规则视为当时的审核结论。",
            ),
        ),
    )


def _risk_assessment_response(
    snapshot: RiskAssessmentSnapshot,
) -> RiskAssessmentResponse:
    """Serialize the exact persisted snapshot into the public response shape."""
    return cast(RiskAssessmentResponse, snapshot.model_dump(mode="json"))


def _public_risk_field(
    finding: ContentRiskFinding,
) -> Literal["title", "body", "tags"] | None:
    if finding.field in {"title", "body"}:
        return cast(Literal["title", "body"], finding.field)
    if finding.field == "tags" or finding.field.startswith("tags["):
        return "tags"
    return None


def _invalid_form_error() -> APIError:
    return APIError(
        code="INVALID_FORM_DATA",
        message="表单字段无效，请检查后重试。",
        status_code=400,
    )


def _user_id(user: AuthenticatedUser | None) -> int | None:
    return None if user is None else user.user_id


def _image_preview_url(generation_id: str) -> str:
    """Build a same-API relative URL without trusting request host headers."""
    return f"/api/v1/generations/{generation_id}/image-preview"


def _valid_stored_preview(
    preview: StoredImagePreview,
    *,
    generation_id: str,
    user_id: int | None,
) -> bool:
    """Fail closed if an adapter returns corrupt or cross-owner image data."""
    if (
        not isinstance(preview, StoredImagePreview)
        or preview.generation_id != generation_id
        or preview.user_id != user_id
        or type(preview.content) is not bytes
        or not 0 < len(preview.content) <= PREVIEW_MAX_BYTES
    ):
        return False
    if preview.media_type == "image/webp":
        return (
            len(preview.content) >= 12
            and preview.content[:4] == b"RIFF"
            and preview.content[8:12] == b"WEBP"
        )
    if preview.media_type == "image/jpeg":
        return (
            preview.content.startswith(b"\xff\xd8\xff")
            and preview.content.endswith(b"\xff\xd9")
        )
    return False


def _generation_not_found_error() -> APIError:
    """Use one response for missing, deleted, failed, and wrong-owner records."""
    return APIError(
        code="GENERATION_NOT_FOUND",
        message="历史记录不存在或已不可用。",
        status_code=404,
        headers=HISTORY_CACHE_HEADERS,
    )


def _history_database_error() -> APIError:
    """Build one safe response for history read and deletion failures."""
    return APIError(
        code="DATABASE_ERROR",
        message="历史记录暂时无法处理，请稍后重试。",
        status_code=500,
        retryable=True,
        headers=HISTORY_CACHE_HEADERS,
    )


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
