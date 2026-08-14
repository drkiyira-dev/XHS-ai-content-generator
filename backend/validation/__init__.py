"""Member-C copy validation adapted to B's generated field names.

The validation rules originate from member C's PR #1 at commit ``b4ca421``.
They remain independent from FastAPI and are run again before a success record
may be written to the database.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from backend.schemas import BusinessException, ErrorCode


MAX_TITLE_LENGTH = 20
MAX_IMAGE_SUMMARY_LENGTH = 2_000
MAX_BODY_LENGTH = 10_000
# Length of the normalized public tag, including its single leading ``#``.
MAX_TAG_LENGTH = 100
MIN_TAGS_COUNT = 3
MAX_TAGS_COUNT = 5


def normalize_tags(tags: Iterable[Any] | None) -> list[str]:
    """Trim, de-duplicate, and prefix tags while preserving their order."""
    if not isinstance(tags, (list, tuple)):
        raise BusinessException(
            ErrorCode.VALIDATION_ERROR,
            "文案规则校验失败",
            {"tags": "标签必须是列表"},
        )

    normalized_tags: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if not isinstance(tag, str):
            raise BusinessException(
                ErrorCode.VALIDATION_ERROR,
                "文案规则校验失败",
                {"tags": "每个标签都必须是字符串"},
            )
        normalized = tag.strip().strip("#").strip()
        if not normalized or normalized in seen:
            continue
        if len(f"#{normalized}") > MAX_TAG_LENGTH:
            raise BusinessException(
                ErrorCode.VALIDATION_ERROR,
                "文案规则校验失败",
                {"tags": f"每个标签不能超过 {MAX_TAG_LENGTH} 字"},
            )
        seen.add(normalized)
        normalized_tags.append(f"#{normalized}")
    return normalized_tags


def validate_copy(
    *,
    image_summary: Any = None,
    image_description: Any = None,
    title: Any = None,
    body: Any = None,
    content: Any = None,
    tags: Iterable[Any] | None = None,
) -> tuple[str, str, str, list[str]]:
    """Validate and normalize one generated copy before persistence."""
    errors: dict[str, str] = {}

    raw_summary = image_summary if image_summary is not None else image_description
    normalized_summary = _normalize_required_text(
        raw_summary,
        field="image_summary",
        empty_message="图片描述不能为空",
        errors=errors,
    )
    if (
        normalized_summary
        and len(normalized_summary) > MAX_IMAGE_SUMMARY_LENGTH
    ):
        errors["image_summary"] = (
            f"图片描述不能超过 {MAX_IMAGE_SUMMARY_LENGTH} 字"
        )

    normalized_title = _normalize_required_text(
        title,
        field="title",
        empty_message="标题不能为空",
        errors=errors,
    )
    if normalized_title and len(normalized_title) > MAX_TITLE_LENGTH:
        errors["title"] = f"标题不能超过 {MAX_TITLE_LENGTH} 字"

    raw_body = body if body is not None else content
    normalized_body = _normalize_required_text(
        raw_body,
        field="body",
        empty_message="正文不能为空",
        errors=errors,
    )
    if normalized_body and len(normalized_body) > MAX_BODY_LENGTH:
        errors["body"] = f"正文不能超过 {MAX_BODY_LENGTH} 字"

    normalized_tags = normalize_tags(tags)
    if not MIN_TAGS_COUNT <= len(normalized_tags) <= MAX_TAGS_COUNT:
        errors["tags"] = (
            f"标签数量必须在 {MIN_TAGS_COUNT} 到 {MAX_TAGS_COUNT} 个之间"
        )

    if errors:
        raise BusinessException(
            ErrorCode.VALIDATION_ERROR,
            "文案规则校验失败",
            errors,
        )

    return (
        normalized_summary,
        normalized_title,
        normalized_body,
        normalized_tags,
    )


def _normalize_required_text(
    value: Any,
    *,
    field: str,
    empty_message: str,
    errors: dict[str, str],
) -> str:
    """Normalize one text field without coercing arbitrary private values."""
    if value is not None and not isinstance(value, str):
        errors[field] = "字段必须是字符串"
        return ""
    normalized = "" if value is None else value.strip()
    if not normalized:
        errors[field] = empty_message
    return normalized


def validate_generation_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Validate B or C field aliases and return one normalized dictionary."""
    if not isinstance(result, Mapping):
        raise BusinessException(
            ErrorCode.VALIDATION_ERROR,
            "生成结果格式错误",
        )

    summary, title, body, tags = validate_copy(
        image_summary=result.get("image_summary"),
        image_description=result.get("image_description"),
        title=result.get("title"),
        body=result.get("body"),
        content=result.get("content"),
        tags=result.get("tags"),
    )
    return {
        "image_description": summary,
        "image_summary": summary,
        "title": title,
        "content": body,
        "body": body,
        "tags": tags,
    }


__all__ = [
    "MAX_BODY_LENGTH",
    "MAX_IMAGE_SUMMARY_LENGTH",
    "MAX_TAG_LENGTH",
    "MAX_TAGS_COUNT",
    "MAX_TITLE_LENGTH",
    "MIN_TAGS_COUNT",
    "normalize_tags",
    "validate_copy",
    "validate_generation_result",
]
