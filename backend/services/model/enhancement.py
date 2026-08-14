"""Deterministic, evidence-preserving copy enhancement helpers.

The enhancer deliberately does not infer a category or make a second model
request.  A caller must select the emoji level and may provide a known content
category.  Only allow-listed, single-code-point emoji are inserted; existing
text is never rewritten to make room for decoration.
"""

from enum import StrEnum
from types import MappingProxyType
from typing import Final, Mapping

from backend.services.model.types import (
    GENERATED_TITLE_MAX_LENGTH,
    GeneratedCopy,
)


class EmojiLevel(StrEnum):
    """Supported amounts of deterministic emoji decoration."""

    OFF = "off"
    LIGHT = "light"
    EXPRESSIVE = "expressive"


class ContentCategory(StrEnum):
    """Explicit categories used only to select local emoji and tag entries."""

    GENERAL = "general"
    TRAVEL = "travel"
    FOOD = "food"
    BEAUTY = "beauty"
    FASHION = "fashion"
    HOME = "home"
    PETS = "pets"


RELATED_TAG_CATALOG_VERSION: Final = "2026-08-14.1"
CONTENT_CATEGORY_RULES_VERSION: Final = "2026-08-14.1"
MAX_TAGS: Final = 5

# No variation selectors, skin-tone modifiers, or ZWJ sequences are allowed in
# values that this module inserts.  This keeps title accounting compatible with
# the existing Python-code-point limit.
EMOJI_WHITELIST: Final = frozenset(
    {
        "✨",
        "🌿",
        "🍜",
        "🏠",
        "🐾",
        "👗",
        "📷",
        "📝",
        "🪴",
    }
)

_EMOJI_LIMITS: Final = MappingProxyType(
    {
        EmojiLevel.OFF: (0, 0),
        EmojiLevel.LIGHT: (1, 2),
        EmojiLevel.EXPRESSIVE: (2, 3),
    }
)

# Each profile has title candidates followed by body candidates.  The
# candidates are deliberately neutral: none asserts popularity, verification,
# price, ranking, medical efficacy, or product performance.
_EMOJI_PROFILES: Final = MappingProxyType(
    {
        ContentCategory.GENERAL: (("📷", "✨"), ("📝", "📷", "✨")),
        ContentCategory.TRAVEL: (("🌿", "📷"), ("📷", "🌿", "✨")),
        ContentCategory.FOOD: (("🍜", "📷"), ("📷", "🍜", "✨")),
        ContentCategory.BEAUTY: (("✨", "📷"), ("📝", "✨", "📷")),
        ContentCategory.FASHION: (("👗", "📷"), ("📷", "👗", "✨")),
        ContentCategory.HOME: (("🏠", "🪴"), ("🪴", "🏠", "✨")),
        ContentCategory.PETS: (("🐾", "📷"), ("📷", "🐾", "✨")),
    }
)

RELATED_TAG_CATALOG: Final[Mapping[ContentCategory, tuple[str, ...]]] = (
    MappingProxyType(
        {
            ContentCategory.GENERAL: (
                "#图片记录",
                "#图文分享",
                "#生活记录",
                "#日常分享",
                "#内容记录",
            ),
            ContentCategory.TRAVEL: (
                "#风景记录",
                "#旅行随拍",
                "#自然风光",
                "#旅途见闻",
                "#户外记录",
            ),
            ContentCategory.FOOD: (
                "#美食记录",
                "#餐桌日常",
                "#今日美食",
                "#饮食记录",
                "#食物摄影",
            ),
            ContentCategory.BEAUTY: (
                "#美妆记录",
                "#护肤记录",
                "#产品记录",
                "#包装分享",
                "#日常护理",
            ),
            ContentCategory.FASHION: (
                "#穿搭记录",
                "#日常穿搭",
                "#服饰分享",
                "#造型记录",
                "#穿搭灵感",
            ),
            ContentCategory.HOME: (
                "#家居记录",
                "#居家日常",
                "#空间记录",
                "#家居分享",
                "#生活空间",
            ),
            ContentCategory.PETS: (
                "#宠物记录",
                "#萌宠日常",
                "#毛孩子日常",
                "#宠物摄影",
                "#陪伴日常",
            ),
        }
    )
)

# Category inference is intentionally conservative.  A unique category must
# match at least one explicit keyword; ambiguous and unmatched copy falls back
# to GENERAL rather than guessing.  GENERAL therefore has no keyword list.
_CONTENT_CATEGORY_KEYWORDS: Final[
    Mapping[ContentCategory, tuple[str, ...]]
] = MappingProxyType(
    {
        ContentCategory.TRAVEL: (
            "风景",
            "旅行",
            "旅途",
            "户外",
            "湖泊",
            "湖景",
            "山景",
            "树林",
            "海边",
            "草原",
            "自然风光",
        ),
        ContentCategory.FOOD: (
            "美食",
            "餐桌",
            "甜品",
            "咖啡",
            "面包",
            "餐厅",
            "饮品",
            "食物",
        ),
        ContentCategory.BEAUTY: (
            "美妆",
            "护肤",
            "卸妆",
            "洁面",
            "面霜",
            "精华",
            "口红",
            "化妆品",
        ),
        ContentCategory.FASHION: (
            "穿搭",
            "服饰",
            "连衣裙",
            "外套",
            "衬衫",
            "造型",
        ),
        ContentCategory.HOME: (
            "家居",
            "居家",
            "客厅",
            "卧室",
            "厨房",
            "家具",
            "生活空间",
        ),
        ContentCategory.PETS: (
            "宠物",
            "萌宠",
            "猫咪",
            "狗狗",
            "毛孩子",
        ),
    }
)

# These markers either claim unverified popularity/ranking or turn a generic
# draft into an unconditional recommendation.  The enhancer never appends a
# catalog candidate containing one; scanning model-provided tags is a separate
# risk-reporting concern, so existing tags are left intact.
_UNSUPPORTED_TAG_MARKERS: Final = (
    "爆款",
    "热门推荐",
    "必买",
    "闭眼入",
    "销量第一",
    "全网第一",
)


def enhance_generated_copy(
    generated: GeneratedCopy,
    *,
    emoji_level: EmojiLevel,
    category: ContentCategory = ContentCategory.GENERAL,
    include_related_tags: bool = False,
) -> GeneratedCopy:
    """Return a new copy with bounded emoji and local related tags.

    ``image_summary`` is preserved verbatim because it is an evidence field.
    Existing title/body text is also preserved; the function only adds neutral
    emoji when the selected level has remaining budget.  Existing model tags
    are never removed or replaced.  When explicitly enabled, safe local tags
    are appended in stable order until the existing five-tag limit is reached.
    """
    if not isinstance(generated, GeneratedCopy):
        raise TypeError("generated must be a GeneratedCopy")
    if not isinstance(emoji_level, EmojiLevel):
        raise TypeError("emoji_level must be an EmojiLevel")
    if not isinstance(category, ContentCategory):
        raise TypeError("category must be a ContentCategory")
    if not isinstance(include_related_tags, bool):
        raise TypeError("include_related_tags must be a bool")

    title = generated.title
    body = generated.body
    if emoji_level is not EmojiLevel.OFF:
        title_limit, body_limit = _EMOJI_LIMITS[emoji_level]
        title_candidates, body_candidates = _EMOJI_PROFILES[category]
        title = _decorate_title(title, title_candidates, title_limit)
        body = _decorate_body(body, body_candidates, body_limit)

    tags = (
        _supplement_related_tags(generated.tags, category)
        if include_related_tags
        else generated.tags
    )
    return GeneratedCopy(
        image_summary=generated.image_summary,
        title=title,
        body=body,
        tags=tags,
    )


def infer_content_category(generated: GeneratedCopy) -> ContentCategory:
    """Infer one unambiguous local category or conservatively return GENERAL."""
    if not isinstance(generated, GeneratedCopy):
        raise TypeError("generated must be a GeneratedCopy")

    # Use the authored copy fields rather than the evidence summary.  A summary
    # may mention incidental background objects from several categories; those
    # should not silently steer stylistic decoration or related tags.
    searchable_text = "\n".join(
        (generated.title, generated.body, *generated.tags)
    ).casefold()
    matches = {
        category
        for category, keywords in _CONTENT_CATEGORY_KEYWORDS.items()
        if any(keyword.casefold() in searchable_text for keyword in keywords)
    }
    if len(matches) != 1:
        return ContentCategory.GENERAL
    return next(iter(matches))


def _decorate_title(
    title: str,
    candidates: tuple[str, ...],
    limit: int,
) -> str:
    result = title
    emoji_count = _probable_emoji_count(result)
    for emoji in candidates:
        if emoji_count >= limit:
            break
        if emoji in result:
            continue
        candidate = (
            f"{emoji} {result}"
            if emoji_count == 0
            else f"{result} {emoji}"
        )
        if len(candidate) > GENERATED_TITLE_MAX_LENGTH:
            continue
        result = candidate
        emoji_count += 1
    return result


def _decorate_body(
    body: str,
    candidates: tuple[str, ...],
    limit: int,
) -> str:
    result = body
    emoji_count = _probable_emoji_count(result)
    for emoji in candidates:
        if emoji_count >= limit:
            break
        if emoji in result:
            continue
        result = (
            f"{emoji} {result}"
            if emoji_count == 0
            else f"{result} {emoji}"
        )
        emoji_count += 1
    return result


def _probable_emoji_count(value: str) -> int:
    """Conservatively count emoji bases so existing emoji consume budget."""
    return sum(1 for character in value if _is_probable_emoji_base(character))


def _is_probable_emoji_base(character: str) -> bool:
    codepoint = ord(character)
    return (
        character in EMOJI_WHITELIST
        or 0x1F000 <= codepoint <= 0x1FAFF
        or 0x2600 <= codepoint <= 0x27BF
    )


def _supplement_related_tags(
    source_tags: tuple[str, ...],
    category: ContentCategory,
) -> tuple[str, ...]:
    supplemented = list(source_tags)
    if len(supplemented) >= MAX_TAGS:
        return tuple(supplemented)
    seen = {tag.casefold() for tag in source_tags}

    candidates = (
        *RELATED_TAG_CATALOG[category],
        *RELATED_TAG_CATALOG[ContentCategory.GENERAL],
    )
    for raw_tag in candidates:
        if len(supplemented) >= MAX_TAGS:
            break
        tag = _normalize_tag(raw_tag)
        comparison_key = tag.casefold()
        if (
            not tag
            or comparison_key in seen
            or _is_unsupported_promotional_tag(tag)
        ):
            continue
        supplemented.append(tag)
        seen.add(comparison_key)

    return tuple(supplemented)


def _normalize_tag(value: str) -> str:
    normalized = value.strip().strip("#").strip()
    return f"#{normalized}" if normalized else ""


def _is_unsupported_promotional_tag(tag: str) -> bool:
    compact = "".join(tag.split()).casefold()
    return any(marker.casefold() in compact for marker in _UNSUPPORTED_TAG_MARKERS)


if any(len(emoji) != 1 for emoji in EMOJI_WHITELIST):
    raise RuntimeError("emoji enhancement whitelist must contain single code points")


__all__ = [
    "CONTENT_CATEGORY_RULES_VERSION",
    "ContentCategory",
    "EMOJI_WHITELIST",
    "EmojiLevel",
    "RELATED_TAG_CATALOG",
    "RELATED_TAG_CATALOG_VERSION",
    "enhance_generated_copy",
    "infer_content_category",
]
