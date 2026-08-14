"""Static regression checks for routed image-summary disclosure controls."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
APP_VUE = ROOT / "frontend" / "src" / "App.vue"
GENERATE_VIEW = ROOT / "frontend" / "src" / "views" / "GenerateView.vue"
HISTORY_VIEW = ROOT / "frontend" / "src" / "views" / "HistoryView.vue"
WORKSPACE_STATE = ROOT / "frontend" / "src" / "state" / "workspace.ts"


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


def test_current_and_history_summaries_use_native_closed_disclosures() -> None:
    generate_source = GENERATE_VIEW.read_text(encoding="utf-8")
    history_source = HISTORY_VIEW.read_text(encoding="utf-8")

    current = _disclosure_for(generate_source, "currentVersion.server.image_summary")
    history = _disclosure_for(history_source, "item.image_summary")

    for disclosure in (current, history):
        opening_tag = disclosure.split(">", 1)[0]
        assert " open" not in opening_tag
        assert ":open" not in opening_tag
        assert 'role="button"' not in disclosure
        assert "aria-expanded" not in disclosure
        assert "@click" not in disclosure
        assert "@toggle" not in disclosure

    assert ':aria-label="`图片理解摘要：${item.title}`"' in history


def test_summary_is_a_read_only_server_field_and_does_not_change_copy_contract() -> None:
    app_source = APP_VUE.read_text(encoding="utf-8")
    generate_source = GENERATE_VIEW.read_text(encoding="utf-8")
    history_source = HISTORY_VIEW.read_text(encoding="utf-8")
    workspace_source = WORKSPACE_STATE.read_text(encoding="utf-8")

    assert "{{ currentVersion.server.image_summary }}" in generate_source
    assert "{{ item.image_summary }}" in history_source
    for source in (generate_source, history_source):
        assert "v-html" not in source
        assert "innerHTML" not in source

    draft_contract = re.search(
        r"export interface EditableGenerationDraft \{(?P<body>.*?)\n\}",
        workspace_source,
        flags=re.DOTALL,
    )
    assert draft_contract is not None
    assert "image_summary" not in draft_contract.group("body")
    assert 'v-model="currentVersion.server.image_summary"' not in generate_source
    assert "currentVersion.draft.image_summary" not in generate_source

    copy_generation = _function_body(app_source, "copyGeneration")
    copy_draft = _function_body(app_source, "copyDraft")
    assert "generation.title" in copy_generation
    assert "generation.body" in copy_generation
    assert "generation.tags" in copy_generation
    assert "generation.image_summary" not in copy_generation
    assert "draft.title" in copy_draft
    assert "draft.body" in copy_draft
    assert "draft.tags" in copy_draft
    assert "draft.image_summary" not in copy_draft


def test_disclosures_have_mobile_safe_text_and_keyboard_focus_styles() -> None:
    for path in (GENERATE_VIEW, HISTORY_VIEW):
        source = path.read_text(encoding="utf-8")
        assert ".image-summary-details summary" in source
        assert "min-height: 44px" in source
        assert ".image-summary-details summary:focus-visible" in source
        assert "white-space: pre-wrap" in source
        assert "overflow-wrap: anywhere" in source
