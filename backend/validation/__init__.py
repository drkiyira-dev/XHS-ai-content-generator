"""Validation package: 文案合规校验。

核心导出：
    validate_copy(image_description, title, content, tags)
        -> (norm_desc, norm_title, norm_content, norm_tags)
        不合规则抛 BusinessException(VALIDATION_ERROR)，不会把脏数据交给前端/写库。

    validate_generation_result(dict) -> dict
        对 LLM 返回整包对象做校验并返回规范化 dict。

规则：
    - image_description 非空
    - title 非空 且 ≤20 字
    - content 非空
    - tags 去重并补 # 前缀后数量在 3~5 个
"""
from typing import List, Dict, Any, Tuple

from ..schemas import ErrorCode, BusinessException


MAX_TITLE_LENGTH = 20
MIN_TAGS_COUNT = 3
MAX_TAGS_COUNT = 5


def _normalize_tag(tag: str) -> str:
    if tag is None:
        return ""
    return str(tag).strip().strip("#").strip()


def normalize_tags(tags: List[str]) -> List[str]:
    """去重并在每个标签前补 #，保持原顺序。空标签自动丢弃。"""
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


def validate_copy(
    image_description: str,
    title: str,
    content: str,
    tags: List[str],
) -> Tuple[str, str, str, List[str]]:
    """校验并规范化四件套。不合规则抛出 VALIDATION_ERROR。"""
    errors: Dict[str, str] = {}

    image_description = (image_description or "").strip()
    if not image_description:
        errors["image_description"] = "图片描述不能为空"

    title = (title or "").strip()
    if not title:
        errors["title"] = "标题不能为空"
    elif len(title) > MAX_TITLE_LENGTH:
        errors["title"] = f"标题超过 {MAX_TITLE_LENGTH} 字限制（当前 {len(title)} 字）"

    content = (content or "").strip()
    if not content:
        errors["content"] = "正文不能为空"

    norm_tags = normalize_tags(tags or [])
    n = len(norm_tags)
    if n < MIN_TAGS_COUNT or n > MAX_TAGS_COUNT:
        errors["tags"] = f"标签数量需在 {MIN_TAGS_COUNT}~{MAX_TAGS_COUNT} 个之间（当前 {n} 个，已去重补#）"

    if errors:
        raise BusinessException(ErrorCode.VALIDATION_ERROR, "文案规则校验失败", errors)

    return image_description, title, content, norm_tags


def validate_generation_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """校验 LLM 返回的整包 dict，返回规范化后的字段集合。"""
    if not isinstance(result, dict):
        raise BusinessException(ErrorCode.VALIDATION_ERROR, "生成结果格式错误（预期对象）")

    desc, title, content, tags = validate_copy(
        result.get("image_description") or "",
        result.get("title") or "",
        result.get("content") or "",
        result.get("tags") or [],
    )
    return {
        "image_description": desc,
        "title": title,
        "content": content,
        "tags": tags,
    }
