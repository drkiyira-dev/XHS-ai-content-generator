"""文案校验与规范化模块

规则：
- 描述（image_description）非空
- 标题 ≤ 20 字
- 正文 非空
- 标签 3~5 个，去重并补 # 前缀（如无）
"""
from typing import List, Dict, Any, Tuple
from app.schemas import ErrorCode, BusinessException


MAX_TITLE_LENGTH = 20
MIN_TAGS_COUNT = 3
MAX_TAGS_COUNT = 5


def _normalize_tag(tag: str) -> str:
    """去除空白，去掉首尾多余的 #，并统一格式。"""
    if tag is None:
        return ""
    t = tag.strip().strip("#").strip()
    return t


def normalize_tags(tags: List[str]) -> List[str]:
    """去重并补 # 前缀，保持原顺序。"""
    if not tags:
        return []
    seen = set()
    result = []
    for t in tags:
        norm = _normalize_tag(t)
        if not norm:
            continue
        if norm in seen:
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
    """校验并规范化 LLM 返回的文案结果。不合规则将抛出 BusinessException，不会交给前端。"""
    errors = {}

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
        errors["tags"] = f"标签数量需在 {MIN_TAGS_COUNT}~{MAX_TAGS_COUNT} 个之间（当前 {n} 个，需去重）"

    if errors:
        raise BusinessException(
            ErrorCode.VALIDATION_ERROR,
            "文案规则校验失败",
            errors,
        )

    return image_description, title, content, norm_tags


def validate_generation_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """对 LLM 输出的字典结构进行校验并返回规范化结果。"""
    if not isinstance(result, dict):
        raise BusinessException(ErrorCode.VALIDATION_ERROR, "生成结果格式错误，预期为对象")

    image_description = result.get("image_description") or ""
    title = result.get("title") or ""
    content = result.get("content") or ""
    tags = result.get("tags") or []

    image_description, title, content, tags = validate_copy(
        image_description, title, content, tags
    )

    return {
        "image_description": image_description,
        "title": title,
        "content": content,
        "tags": tags,
    }
