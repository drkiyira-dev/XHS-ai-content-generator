"""Static regression checks for the routed product landing page."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
HOME_VIEW = ROOT / "frontend" / "src" / "views" / "HomeView.vue"
LANDING_VUE = ROOT / "frontend" / "src" / "components" / "LandingPage.vue"
APP_VUE = ROOT / "frontend" / "src" / "App.vue"


def _function_body(source: str, function_name: str) -> str:
    match = re.search(
        rf"^(?P<indent>[ \t]*)(?:async\s+)?function {function_name}"
        rf"\([^)]*\)(?:\s*:\s*[^{{\n]+)?\s*\{{"
        rf"(?P<body>.*?)^(?P=indent)\}}",
        source,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert match is not None, function_name
    return match.group("body")


def test_home_route_view_owns_landing_navigation_without_business_resets() -> None:
    home_source = HOME_VIEW.read_text(encoding="utf-8")
    landing_source = LANDING_VUE.read_text(encoding="utf-8")
    start_generation = _function_body(home_source, "startGeneration")

    assert "import LandingPage" in home_source
    assert '<LandingPage @start="startGeneration" />' in home_source
    assert "router.push({ name: 'generate' })" in start_generation
    assert 'id="hero-generate-cta"' in landing_source
    assert 'id="closing-generate-cta"' in landing_source
    assert landing_source.count("@click=\"emit('start')\"") == 2
    for forbidden_business_state in (
        "clearImage",
        "imageFile.value",
        "imagePreviewUrl.value",
        "form.",
        "status.value",
        "Object.assign(result",
        "historyItems.value",
    ):
        assert forbidden_business_state not in start_generation


def test_application_brand_uses_the_icon_library_book_mark() -> None:
    source = APP_VUE.read_text(encoding="utf-8")

    assert "import { Reading } from '@element-plus/icons-vue'" in source
    assert '<span class="brand-mark" aria-hidden="true"><Reading /></span>' in source
    assert '<span aria-hidden="true">图</span>' not in source
    assert ".brand-mark svg" in source


def test_landing_has_one_focusable_page_title_and_labelled_product_sections() -> None:
    source = LANDING_VUE.read_text(encoding="utf-8")

    assert source.count("<h1") == 1
    assert '<h1 id="page-title" tabindex="-1">' in source
    assert 'aria-labelledby="page-title"' in source
    for section_id, heading_id in (
        ("landing-features", "features-title"),
        ("landing-process", "process-title"),
        ("landing-faq", "faq-title"),
    ):
        assert f'id="{section_id}"' in source
        assert f'aria-labelledby="{heading_id}"' in source
        assert f'id="{heading_id}"' in source


def test_landing_has_every_required_product_section_and_semantic_faq() -> None:
    source = LANDING_VUE.read_text(encoding="utf-8")

    for visible_text in (
        "工具介绍",
        "功能亮点",
        "使用流程",
        "常见问题",
        "开始创作",
        "从一张图片",
        "主题或名称",
        "本地账号演示",
        "图片生成时由后端发送至第三方视觉模型",
        "API Key 仅保存在后端",
    ):
        assert visible_text in source

    for obsolete_narrow_copy in (
        "商品图",
        "商品名",
        "产品名",
        "小红书文案生成平台",
    ):
        assert obsolete_narrow_copy not in source

    faq_block = re.search(
        r"const FAQ_ITEMS = \[(.*?)\] as const",
        source,
        flags=re.DOTALL,
    )
    assert faq_block is not None
    assert faq_block.group(1).count("question:") >= 4
    assert faq_block.group(1).count("question:") == faq_block.group(1).count(
        "answer:"
    )
    assert '<details v-for="item in FAQ_ITEMS"' in source
    assert re.search(
        r"<summary>\s*<span>\{\{ item\.question \}\}</span>.*?</summary>",
        source,
        flags=re.DOTALL,
    )
    assert '<Plus class="faq-icon faq-icon--closed"' in source
    assert '<Minus class="faq-icon faq-icon--open"' in source
    assert "<p>{{ item.answer }}</p>" in source


def test_landing_has_responsive_keyboard_and_reduced_motion_baselines() -> None:
    source = LANDING_VUE.read_text(encoding="utf-8")

    assert "font-size: clamp(" in source
    assert "min-height: 48px" in source
    assert ":focus-visible" in source
    assert "@media (prefers-reduced-motion: reduce)" in source
    assert "@media (max-width: 900px)" in source
    assert "@media (max-width: 640px)" in source
    assert 'role="list"' in source
    assert 'role="listitem"' in source


def test_landing_motion_is_progressive_bounded_and_cleaned_up() -> None:
    source = LANDING_VUE.read_text(encoding="utf-8")
    update_progress = _function_body(source, "updateScrollProgress")

    assert "requestAnimationFrame(updateScrollProgress)" in source
    assert "Math.min(1, Math.max(0, rawProgress))" in update_progress
    assert "new IntersectionObserver(" in source
    assert "new ResizeObserver(scheduleProgressUpdate)" in source
    assert "if (!root || reducedMotion || !supportsIntersectionObserver)" in source
    assert source.index("revealObserver?.observe(target)") < source.index(
        "root.classList.add('motion-ready')"
    )
    assert "revealObserver?.disconnect()" in source
    assert "landingResizeObserver?.disconnect()" in source
    assert "window.removeEventListener('scroll', scheduleProgressUpdate)" in source
    assert "window.removeEventListener('resize', scheduleProgressUpdate)" in source
    assert "window.cancelAnimationFrame(progressFrame)" in source
    assert '<div class="landing-progress" aria-hidden="true">' in source
    assert "@media print" in source
    assert '<details v-for="item in FAQ_ITEMS" :key="item.question">' in source
    assert '<section class="final-cta" aria-labelledby="cta-title">' in source


def test_landing_generation_showcase_loops_and_can_be_paused() -> None:
    source = LANDING_VUE.read_text(encoding="utf-8")
    stop_demo = _function_body(source, "stopGenerationDemo")
    start_demo = _function_body(source, "startGenerationDemo")
    unmount = re.search(
        r"onBeforeUnmount\(\(\) => \{(?P<body>.*?)\n\}\)",
        source,
        flags=re.DOTALL,
    )

    assert source.count("shortLabel:") == 4
    assert "SIMULATED FLOW / 模拟流程示意" in source
    assert "LIVE DEMO" not in source
    assert "首页不会调用模型" in source
    assert "也不代表真实耗时、识别结果或性能" in source
    assert "prefers-reduced-motion: reduce" in start_demo
    assert "generationDemoIndex.value = GENERATION_DEMO_STEPS.length - 1" in start_demo
    assert start_demo.index("return") < start_demo.index("window.setTimeout")
    assert "window.setTimeout" in start_demo
    assert "(generationDemoIndex.value + 1) % GENERATION_DEMO_STEPS.length" in start_demo
    assert "window.clearTimeout(generationDemoTimer)" in stop_demo
    assert "generationDemoRunId += 1" in stop_demo
    assert "if (runId !== generationDemoRunId)" in start_demo
    assert start_demo.index("stopGenerationDemo()") < start_demo.index(
        "window.setTimeout"
    )
    assert "startGenerationDemo()" in source
    assert 'class="generation-showcase"' in source
    assert "generationDemoObserver !== observer" in source
    assert "generationDemoVisible.value = entries.some(entry => entry.isIntersecting)" in source
    assert "if (!generationDemoVisible.value)" in source
    assert source.index("generationDemoObserver = observer") < source.index(
        "observer.observe(showcase)"
    )
    assert "{ threshold: 0.2 }" in source
    assert '<Transition name="generation-swap" mode="out-in">' in source
    assert 'v-if="!prefersReducedGenerationMotion"' in source
    assert '@click="toggleGenerationDemo"' in source
    assert "generationDemoPaused.value = !generationDemoPaused.value" in source
    assert "generationDemoPaused ? '继续演示' : '暂停演示'" in source
    assert 'aria-describedby="generation-showcase-description"' in source
    assert '<div class="generation-showcase__visual" aria-hidden="true">' in source
    assert '<ol class="sr-only">' in source
    assert "handleGenerationMotionChange" in source
    assert "removeEventListener('change', handleGenerationMotionChange)" in source
    assert unmount is not None
    assert "stopGenerationDemo()" in unmount.group("body")


def test_landing_uses_only_local_safe_rendering() -> None:
    vue_sources = {
        path: path.read_text(encoding="utf-8")
        for path in (ROOT / "frontend" / "src").rglob("*.vue")
    }

    for path, source in vue_sources.items():
        for unsafe_html_api in (
            "v-html",
            "innerHTML",
            "insertAdjacentHTML",
            "document.write",
        ):
            assert unsafe_html_api not in source, path

        assert "<script src=" not in source, path
        assert "<iframe" not in source, path
        assert "@import" not in source, path
        assert "url(http" not in source, path
        assert "analytics" not in source.lower(), path

    index_source = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert '<html lang="zh-CN">' in index_source
    assert "图文种草助手 · 图片生成小红书内容初稿" in index_source
