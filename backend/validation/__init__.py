"""Validation package：文案合规校验（B/C 共用，零 Flask 依赖。

支持字段映射（B 已与 A 对齐的接口字段：
    image_summary  -> 内部 image_description
    body           -> 内部 content

核心导出：
    validate_copy(image_description=..., title=..., content=..., tags=...)
    validate_generation_result(dict)
        dict 支持 {image_summary, body 或 image_description, content
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..schemas import BusinessException, ErrorCode


MAX_TITLE_LENGTH = 20
MIN_TAGS_COUNT = 3
MAX_TAGS_COUNT = 5


def _normalize_tag(tag: Any) -> str:
    if tag is None:
        return ""
    return str(tag).strip().strip("#").strip()


def normalize_tags(tags: List[Any]) -> List[str]:
    """去重 + 补 #，保持原顺序。空丢弃。"""
    if not tags:
        return []
    seen = set()
    result: List[str] = []
    for t in tags:
        norm = _normalize_tag(t)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        result.append(f"#{norm}")
    return result


def _coerce_image_description(
    image_description: Any = None,
    image_summary: Any = None,
) -> str:
    """image_summary 是 image_description 的别名（B 与 A 对齐的字段。两者都给时优先 image_summary。"""
    v = image_summary if image_summary is not None else image_description
    return ("" if v is None else str(v)).strip()


def _coerce_content(content: Any = None, body: Any = None) -> str:
    """body 是 content 的别名（B 与 A 对齐的字段）。两者都给时优先 body。"""
    v = body if body is not None else content
    return ("" if v is None else str(v)).strip()


def validate_copy(
    image_description: str = "",
    title: str = "",
    content: str = "",
    tags: List[Any] | None = None,
    *,
    image_summary: Any = None,
    body: Any = None,
) -> Tuple[str, str, str, List[str]]:
    """校验 & 规范化。不合规则抛 VALIDATION_ERROR。

    参数别名：image_summary -> image_description；body -> content。
    """
    errors: Dict[str, str] = {}

    desc = _coerce_image_description(image_description=image_description, image_summary=image_summary)
    if not desc:
        errors["image_description"] = "图片描述不能为空"
        errors["image_summary"] = "图片描述不能为空"

    title_s = ("" if title is None else str(title)).strip()
    if not title_s:
        errors["title"] = "标题不能为空"
    elif len(title_s) > MAX_TITLE_LENGTH:
        errors["title"] = f"标题超过 {MAX_TITLE_LENGTH} 字限制（当前 {len(title_s)} 字）"

    content_s = _coerce_content(content=content, body=body)
    if not content_s:
        errors["content"] = "正文不能为空"
        errors["body"] = "正文不能为空"

    norm_tags = normalize_tags(tags or [])
    n = len(norm_tags)
    if n < MIN_TAGS_COUNT or n > MAX_TAGS_COUNT:
        errors["tags"] = f"标签数量需在 {MIN_TAGS_COUNT}~{MAX_TAGS_COUNT} 个之间（当前 {n} 个，已去重补#）"

    if errors:
        raise BusinessException(
            ErrorCode.VALIDATION_ERROR,
            "文案规则校验失败",
            errors,
        )

    return desc, title_s, content_s, norm_tags


def validate_generation_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """对 LLM 整包做校验并规范化 dict。支持 A/B 字段名都支持：
        - image_summary 或 image_description
        - body 或 content
        - title / tags
    返回规范化字段：{image_description, title, content, tags}
    """
    if not isinstance(result, dict):
        raise BusinessException(ErrorCode.VALIDATION_ERROR, "生成结果格式错误（预期对象）")
    desc, title_s, content_s, tags = validate_copy(
        image_description=result.get("image_description") or "",
        image_summary=result.get("image_summary") or "",
        title=result.get("title") or "",
        content=result.get("content") or "",
        body=result.get("body") or "",
        tags=result.get("tags") or [],
    )
    return {
        "image_description": desc,
        "image_summary": desc,
        "title": title_s,
        "content": content_s,
        "body": content_s,
        "tags": tags,
    }
