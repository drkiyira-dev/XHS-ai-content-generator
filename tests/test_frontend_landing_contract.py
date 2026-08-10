"""Static regression checks for the product landing page."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
APP_VUE = ROOT / "frontend" / "src" / "App.vue"
LANDING_VUE = ROOT / "frontend" / "src" / "components" / "LandingPage.vue"


def _function_body(source: str, function_name: str) -> str:
    match = re.search(
        rf"function {function_name}\([^)]*\).*?\{{(.*?)\n\}}",
        source,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def test_landing_page_is_the_default_third_view() -> None:
    source = APP_VUE.read_text(encoding="utf-8")

    assert "type ActiveView = 'home' | 'generate' | 'history'" in source
    assert "const activeView = ref<ActiveView>('home')" in source
    for view, button, panel in (
        ("home", "home-nav-button", "home-panel"),
        ("generate", "generator-nav-button", "generator-panel"),
        ("history", "history-nav-button", "history-panel"),
    ):
        assert f'id="{button}"' in source
        assert f'aria-controls="{panel}"' in source
        assert f'v-show="activeView === \'{view}\'"' in source

    landing_source = LANDING_VUE.read_text(encoding="utf-8")
    assert 'id="home-panel"' in landing_source
    assert 'aria-labelledby="home-nav-button"' in landing_source


def test_landing_ctas_preserve_workspace_business_state() -> None:
    app_source = APP_VUE.read_text(encoding="utf-8")
    landing_source = LANDING_VUE.read_text(encoding="utf-8")
    show_generator = _function_body(app_source, "showGenerator")

    assert '@start="showGenerator"' in app_source
    assert 'id="hero-generate-cta"' in landing_source
    assert 'id="closing-generate-cta"' in landing_source
    assert landing_source.count("@click=\"emit('start')\"") == 2
    assert "activeView.value = 'generate'" in show_generator
    for forbidden_business_state in (
        "clearImage",
        "imageFile.value",
        "imagePreviewUrl.value",
        "form.",
        "status.value",
        "Object.assign(result",
        "historyItems.value",
    ):
        assert forbidden_business_state not in show_generator


def test_landing_has_every_required_product_section_and_semantic_faq() -> None:
    source = LANDING_VUE.read_text(encoding="utf-8")

    for visible_text in (
        "产品介绍",
        "功能亮点",
        "使用流程",
        "常见问题",
        "开始生成",
        "进入生成工作台",
        "本地单用户演示",
        "图片生成时由后端发送至第三方视觉模型",
        "API Key 仅保存在后端",
    ):
        assert visible_text in source

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
    assert "<summary>{{ item.question }}</summary>" in source
    assert "<p>{{ item.answer }}</p>" in source


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
    assert "图文种草助手 · 小红书文案生成平台" in index_source
