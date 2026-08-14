"""Tests for deterministic emoji and related-tag enhancement."""

import pytest

from backend.services.model import (
    CONTENT_CATEGORY_RULES_VERSION,
    ContentCategory,
    EMOJI_WHITELIST,
    EmojiLevel,
    GeneratedCopy,
    RELATED_TAG_CATALOG,
    RELATED_TAG_CATALOG_VERSION,
    enhance_generated_copy,
    infer_content_category,
)
from backend.services.model.types import GENERATED_TITLE_MAX_LENGTH


def _copy(
    *,
    image_summary: str = "图片中可见湖泊、树林、远山及天空倒影。",
    title: str = "湖畔的宁静时刻",
    body: str = "湖面映着树林与远山，云层被夕阳染成柔和的粉色。",
    tags: tuple[str, ...] = ("#湖景", "#自然风光", "#旅行记录"),
) -> GeneratedCopy:
    return GeneratedCopy(
        image_summary=image_summary,
        title=title,
        body=body,
        tags=tags,
    )


def _emoji_count(value: str) -> int:
    return sum(character in EMOJI_WHITELIST for character in value)


def test_off_without_related_tags_returns_an_exact_new_model() -> None:
    original = _copy()

    enhanced = enhance_generated_copy(
        original,
        emoji_level=EmojiLevel.OFF,
        category=ContentCategory.TRAVEL,
        include_related_tags=False,
    )

    assert enhanced is not original
    assert enhanced == original


@pytest.mark.parametrize(
    ("level", "title_limit", "body_limit"),
    [
        (EmojiLevel.LIGHT, 1, 2),
        (EmojiLevel.EXPRESSIVE, 2, 3),
    ],
)
def test_active_levels_insert_only_allowlisted_bounded_emoji(
    level: EmojiLevel,
    title_limit: int,
    body_limit: int,
) -> None:
    enhanced = enhance_generated_copy(
        _copy(),
        emoji_level=level,
        category=ContentCategory.TRAVEL,
        include_related_tags=False,
    )

    assert _emoji_count(enhanced.title) == title_limit
    assert _emoji_count(enhanced.body) == body_limit
    assert len(enhanced.title) <= GENERATED_TITLE_MAX_LENGTH
    assert enhanced.image_summary == "图片中可见湖泊、树林、远山及天空倒影。"


def test_existing_emoji_consumes_budget_without_rewriting_original_text() -> None:
    original = _copy(
        title="🔥 湖畔记录",
        body="✅ 湖面映着远山，适合记录这一刻。",
    )

    enhanced = enhance_generated_copy(
        original,
        emoji_level=EmojiLevel.LIGHT,
        category=ContentCategory.TRAVEL,
        include_related_tags=False,
    )

    assert enhanced.title == original.title
    assert enhanced.body.startswith(original.body)
    assert not any(emoji in enhanced.title for emoji in EMOJI_WHITELIST)
    assert _emoji_count(enhanced.body) == 1


def test_full_length_title_is_never_truncated_to_make_room_for_emoji() -> None:
    original = _copy(title="山" * GENERATED_TITLE_MAX_LENGTH)

    enhanced = enhance_generated_copy(
        original,
        emoji_level=EmojiLevel.EXPRESSIVE,
        category=ContentCategory.TRAVEL,
        include_related_tags=False,
    )

    assert enhanced.title == original.title
    assert len(enhanced.title) == GENERATED_TITLE_MAX_LENGTH


@pytest.mark.parametrize("level", list(EmojiLevel))
def test_enhancement_is_idempotent(level: EmojiLevel) -> None:
    once = enhance_generated_copy(
        _copy(),
        emoji_level=level,
        category=ContentCategory.TRAVEL,
        include_related_tags=True,
    )
    twice = enhance_generated_copy(
        once,
        emoji_level=level,
        category=ContentCategory.TRAVEL,
        include_related_tags=True,
    )

    assert twice == once


def test_related_tags_only_append_and_never_replace_model_tags() -> None:
    original = _copy(
        tags=("#小红书爆款", "#Travel", "#travel"),
    )

    enhanced = enhance_generated_copy(
        original,
        emoji_level=EmojiLevel.OFF,
        category=ContentCategory.TRAVEL,
        include_related_tags=True,
    )

    assert enhanced.tags == (
        "#小红书爆款",
        "#Travel",
        "#travel",
        "#风景记录",
        "#旅行随拍",
    )
    assert enhanced.tags[:3] == original.tags


def test_catalog_is_versioned_local_bounded_and_has_no_popularity_claims() -> None:
    assert RELATED_TAG_CATALOG_VERSION == "2026-08-14.1"
    assert CONTENT_CATEGORY_RULES_VERSION == "2026-08-14.1"
    assert set(RELATED_TAG_CATALOG) == set(ContentCategory)

    unsupported_markers = (
        "爆款",
        "热门推荐",
        "必买",
        "闭眼入",
        "销量第一",
        "全网第一",
    )
    for tags in RELATED_TAG_CATALOG.values():
        assert len(tags) == 5
        assert len(set(tags)) == len(tags)
        assert all(tag.startswith("#") for tag in tags)
        assert all(
            marker not in tag
            for marker in unsupported_markers
            for tag in tags
        )


def test_whitelist_contains_only_simple_single_code_points() -> None:
    assert EMOJI_WHITELIST
    assert all(len(emoji) == 1 for emoji in EMOJI_WHITELIST)
    assert EMOJI_WHITELIST.isdisjoint({"🔥", "✅", "💯", "🏆", "🥇", "💰"})


@pytest.mark.parametrize(
    ("keyword", "expected_message"),
    [
        ("emoji_level", "emoji_level must be an EmojiLevel"),
        ("category", "category must be a ContentCategory"),
    ],
)
def test_enhancer_rejects_untyped_runtime_options(
    keyword: str,
    expected_message: str,
) -> None:
    arguments: dict[str, object] = {
        "emoji_level": EmojiLevel.LIGHT,
        "category": ContentCategory.GENERAL,
        "include_related_tags": False,
    }
    arguments[keyword] = "not-an-enum"

    with pytest.raises(TypeError, match=expected_message):
        enhance_generated_copy(_copy(), **arguments)  # type: ignore[arg-type]


def test_enhancer_rejects_non_boolean_related_tag_switch() -> None:
    with pytest.raises(TypeError, match="include_related_tags must be a bool"):
        enhance_generated_copy(
            _copy(),
            emoji_level=EmojiLevel.OFF,
            include_related_tags="false",  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("category", "title", "body", "tags"),
    [
        (
            ContentCategory.TRAVEL,
            "湖景记录",
            "树林倒映在湖泊中。",
            ("#照片", "#记录", "#分享"),
        ),
        (
            ContentCategory.FOOD,
            "餐桌日常",
            "面包和咖啡摆在桌面上。",
            ("#照片", "#记录", "#分享"),
        ),
        (
            ContentCategory.BEAUTY,
            "卸妆产品记录",
            "包装上可见洁面字样。",
            ("#照片", "#记录", "#分享"),
        ),
        (
            ContentCategory.FASHION,
            "今日穿搭",
            "画面中是一件连衣裙。",
            ("#照片", "#记录", "#分享"),
        ),
        (
            ContentCategory.HOME,
            "居家一角",
            "客厅里摆放着木质家具。",
            ("#照片", "#记录", "#分享"),
        ),
        (
            ContentCategory.PETS,
            "猫咪日常",
            "一只猫咪坐在窗边。",
            ("#照片", "#记录", "#分享"),
        ),
    ],
)
def test_category_inference_requires_one_unique_category_match(
    category: ContentCategory,
    title: str,
    body: str,
    tags: tuple[str, ...],
) -> None:
    generated = _copy(
        image_summary="图片中可见与正文一致的主体。",
        title=title,
        body=body,
        tags=tags,
    )
    assert infer_content_category(generated) is category


@pytest.mark.parametrize(
    "generated",
    [
        _copy(
            image_summary="图片中可见一个主体。",
            title="周末记录",
            body="把今天看到的画面记录下来。",
            tags=("#照片", "#记录", "#分享"),
        ),
        _copy(
            image_summary="图片中可见餐桌与旅途场景。",
            title="旅行中的美食",
            body="旅途中在餐厅看到一份甜品。",
            tags=("#照片", "#记录", "#分享"),
        ),
    ],
)
def test_category_inference_falls_back_for_no_match_or_ambiguity(
    generated: GeneratedCopy,
) -> None:
    assert infer_content_category(generated) is ContentCategory.GENERAL


def test_category_inference_ignores_incidental_summary_keywords() -> None:
    generated = _copy(
        image_summary="背景里可见树林和湖泊。",
        title="周末记录",
        body="把今天看到的主体记录下来。",
        tags=("#照片", "#记录", "#分享"),
    )

    assert infer_content_category(generated) is ContentCategory.GENERAL
