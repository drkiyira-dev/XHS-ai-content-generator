"""Static contracts for local-only API configuration and honest Mock controls."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
API_SERVICE = ROOT / "frontend" / "src" / "services" / "api.ts"
GENERATION_SERVICE = ROOT / "frontend" / "src" / "services" / "generation.ts"
APP_COMPONENT = ROOT / "frontend" / "src" / "App.vue"


def _function_body(source: str, function_name: str) -> str:
    match = re.search(
        rf"^(?:export\s+)?(?:async\s+)?function {function_name}\b"
        rf"(?P<body>.*?)"
        rf"(?=^(?:export\s+)?(?:async\s+)?function |\Z)",
        source,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert match is not None, function_name
    return match.group(0)


def test_api_base_url_is_unconditionally_normalized_for_real_and_mock_modes() -> None:
    source = API_SERVICE.read_text(encoding="utf-8")
    normalize = _function_body(source, "normalizeLocalApiBaseUrl")

    assert (
        "export const API_BASE_URL = "
        "normalizeLocalApiBaseUrl(import.meta.env.VITE_API_BASE_URL)"
    ) in source
    assert "export const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'" in source
    assert "USE_MOCK" not in normalize
    assert "VITE_USE_MOCK" not in normalize
    assert "return parsed.origin" in normalize


def test_api_base_url_accepts_only_literal_loopback_http_origins() -> None:
    source = API_SERVICE.read_text(encoding="utf-8")
    normalize = _function_body(source, "normalizeLocalApiBaseUrl")
    hostname = _function_body(source, "hostnameFromAuthority")

    assert "new Set(['localhost', '127.0.0.1', '[::1]'])" in source
    assert "parsed.protocol !== 'http:'" in normalize
    assert "parsed.protocol !== 'https:'" in normalize
    assert "LOOPBACK_API_HOSTS.has(rawHostname)" in normalize
    assert "LOOPBACK_API_HOSTS.has(parsed.hostname.toLowerCase())" in normalize
    assert "authority.includes('@')" in normalize
    assert "candidate !== candidate.trim()" in normalize
    assert "(suffix !== '' && suffix !== '/')" in normalize
    for rejected_component in (
        "parsed.username !== ''",
        "parsed.password !== ''",
        "parsed.pathname !== '/'",
        "parsed.search !== ''",
        "parsed.hash !== ''",
    ):
        assert rejected_component in normalize

    assert "authority.startsWith('[')" in hostname
    assert "return authority.slice(0, closingBracket + 1).toLowerCase()" in hostname
    assert "return authority.slice(0, portSeparator).toLowerCase()" in hostname


def test_mock_requires_the_frozen_preference_fields() -> None:
    source = GENERATION_SERVICE.read_text(encoding="utf-8")
    mock_generate = _function_body(source, "mockGenerate")
    emoji = _function_body(source, "readMockEmojiLevel")
    related = _function_body(source, "readMockRelatedTags")

    assert "readMockEmojiLevel(formData)" in mock_generate
    assert "readMockRelatedTags(formData)" in mock_generate
    assert "getAll('emoji_level')" in emoji
    assert "values.length !== 1" in emoji
    assert "MOCK_EMOJI_LEVELS.has" in emoji
    assert "new Set<EmojiLevel>(['off', 'light', 'expressive'])" in source
    assert "getAll('related_tags')" in related
    assert "values.length !== 1" in related
    assert "values[0] === 'true'" in related
    assert "values[0] === 'false'" in related
    assert "invalidMockFormData()" in emoji
    assert "invalidMockFormData()" in related


def test_mock_enrichment_is_fixed_bounded_and_idempotent() -> None:
    source = GENERATION_SERVICE.read_text(encoding="utf-8")
    mock_generate = _function_body(source, "mockGenerate")
    emoji_style = _function_body(source, "applyMockEmojiStyle")
    append_once = _function_body(source, "appendOnce")
    prefix_once = _function_body(source, "prefixOnce")
    tags = _function_body(source, "buildMockTags")
    subject = _function_body(source, "readMockSubjectName")

    assert "applyMockEmojiStyle(baseTitle, variant.body, emojiLevel)" in mock_generate
    assert "buildMockTags(variant.tags, includeRelatedTags)" in mock_generate
    assert "title: mockCopy.title" in mock_generate
    assert "body: mockCopy.body" in mock_generate
    assert "tags: mockTags" in mock_generate
    assert "Math.random" not in mock_generate

    assert "emojiLevel === 'off'" in emoji_style
    assert "emojiLevel === 'light'" in emoji_style
    assert "appendOnce(title, ' ✨')" in emoji_style
    assert "prefixOnce(" in emoji_style
    assert "value.endsWith(suffix)" in append_once
    assert "value.startsWith(prefix)" in prefix_once

    assert "new Set(variantTags)" in tags
    assert ".slice(0, 5)" in tags
    assert "tags.length < 5" in tags
    assert "!tags.includes(MOCK_RELATED_TAG)" in tags
    assert "tags.push(MOCK_RELATED_TAG)" in tags
    assert "characters.slice(0, 10).join('')" in subject

    mock_section = source[source.index("const MOCK_RELATED_TAG"):source.index("async function realGenerate")]
    assert "热门" not in mock_section
    assert "#小红书爆款" not in mock_section


def test_mock_regeneration_cycles_three_honest_distinct_variants_per_owner() -> None:
    source = GENERATION_SERVICE.read_text(encoding="utf-8")
    mock_generate = _function_body(source, "mockGenerate")

    assert "const MOCK_VARIANTS = Object.freeze([" in source
    for title_suffix in ("交互示例一", "交互示例二", "交互示例三"):
        assert f"titleSuffix: '{title_suffix}'" in source

    assert "本地 Mock 模式不会分析图片" in source
    assert source.count("【交互占位｜未读取图片】") == 3
    assert "#非识图结果" in source
    assert "const mockGenerationSequenceByOwner = new Map<number, number>()" in source
    assert "mockGenerationSequenceByOwner.get(ownerId) ?? 0" in mock_generate
    assert "mockGenerationSequenceByOwner.set(ownerId, sequence + 1)" in mock_generate
    assert "sequence % MOCK_VARIANTS.length" in mock_generate
    assert "mockHistoryFor(ownerId)" in mock_generate
    assert "ownerHistory.length" not in mock_generate
    assert "Math.random" not in mock_generate
    assert "generation_id: `mock-${Date.now()}-${sequence}`" in mock_generate

    app_source = APP_COMPONENT.read_text(encoding="utf-8")
    assert "import { AUTH_ENABLED, USE_MOCK } from './services/api'" in app_source
    assert "USE_MOCK ? 'Mock 演示' : '免登录演示'" in app_source


def test_mock_workspace_never_labels_placeholder_text_as_an_image_answer() -> None:
    view_source = (ROOT / "frontend/src/views/GenerateView.vue").read_text(encoding="utf-8")

    assert "import { USE_MOCK } from '../services/api'" in view_source
    for disclosure in (
        "运行交互演示（不会识图）",
        "Mock 模式只在浏览器内使用图片进行预览和历史缩略图演示",
        "正在准备演示数据",
        "交互演示数据｜未读取上传图片",
        "以下标题、正文、标签和风险状态都是界面占位数据，不是图片生成答案。",
        "交互示例（非图片答案）",
        "风险检查示意（未执行真实检查）",
        "没有执行真实内容风险扫描",
    ):
        assert disclosure in view_source
