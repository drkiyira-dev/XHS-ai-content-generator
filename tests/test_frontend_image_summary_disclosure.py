"""Static regression checks for image-summary disclosure controls."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
APP_VUE = ROOT / "frontend" / "src" / "App.vue"


def _disclosure_for(source: str, binding: str) -> str:
    binding_text = re.escape(f"{{{{ {binding} }}}}")
    match = re.search(
        rf'<details class="image-summary-details">\s*'
        rf'<summary[^>]*>图片理解摘要</summary>\s*'
        rf'<p>{binding_text}</p>\s*</details>',
        source,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def test_current_and_history_summaries_use_native_closed_disclosures() -> None:
    source = APP_VUE.read_text(encoding="utf-8")

    current = _disclosure_for(source, "result.image_summary")
    history = _disclosure_for(source, "item.image_summary")

    for disclosure in (current, history):
        opening_tag = disclosure.split(">", 1)[0]
        assert " open" not in opening_tag
        assert ":open" not in opening_tag
        assert 'role="button"' not in disclosure
        assert "aria-expanded" not in disclosure
        assert "@click" not in disclosure
        assert "@toggle" not in disclosure

    assert ':aria-label="`图片理解摘要：${item.title}`"' in history


def test_summary_text_is_safely_rendered_and_does_not_change_copy_contract() -> None:
    source = APP_VUE.read_text(encoding="utf-8")

    assert "{{ result.image_summary }}" in source
    assert "{{ item.image_summary }}" in source
    assert "v-html" not in source
    assert "innerHTML" not in source

    copy_match = re.search(
        r"async function copyGeneration\([^)]*\).*?\{(.*?)\n\}",
        source,
        flags=re.DOTALL,
    )
    assert copy_match is not None
    assert "generation.title" in copy_match.group(1)
    assert "generation.body" in copy_match.group(1)
    assert "generation.tags" in copy_match.group(1)
    assert "generation.image_summary" not in copy_match.group(1)


def test_disclosure_has_mobile_safe_text_and_keyboard_focus_styles() -> None:
    source = APP_VUE.read_text(encoding="utf-8")

    assert ".image-summary-details summary" in source
    assert "min-height: 44px" in source
    assert ".image-summary-details summary:focus-visible" in source
    assert "white-space: pre-wrap" in source
    assert "overflow-wrap: anywhere" in source
