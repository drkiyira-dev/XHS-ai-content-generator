"""Static regression checks for the local single-user history page."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
APP_VUE = ROOT / "frontend" / "src" / "App.vue"
GENERATION_SERVICE = ROOT / "frontend" / "src" / "services" / "generation.ts"


def _function_body(source: str, function_name: str) -> str:
    match = re.search(
        rf"(?:export )?async function {function_name}\([^)]*\).*?\{{(.*?)\n\}}",
        source,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def test_history_service_uses_read_only_no_store_request() -> None:
    source = GENERATION_SERVICE.read_text(encoding="utf-8")

    assert "export interface GenerationHistoryResponse" in source
    assert "items: GenerationResponse[]" in source
    assert "count: number" in source
    assert "`${API_BASE_URL}/api/v1/generations?limit=${encodeURIComponent(limit)}`" in source
    assert "method: 'GET'" in source
    assert "cache: 'no-store'" in source
    assert "method: 'DELETE'" not in source
    assert "limit < 1 || limit > 50" in source


def test_history_view_loads_independently_from_generation() -> None:
    source = APP_VUE.read_text(encoding="utf-8")
    load_history = _function_body(source, "loadHistory")
    handle_generate = _function_body(source, "handleGenerate")

    assert "type HistoryStatus = 'idle' | 'loading' | 'success' | 'error'" in source
    assert "const HISTORY_LIMIT = 20" in source
    assert "listGenerations(HISTORY_LIMIT)" in load_history
    assert "generate(" not in load_history
    assert "historyLoaded.value = false" in source
    assert "historyRevision += 1" in handle_generate
    assert "activeView.value === 'history'" in handle_generate
    assert "requestedRevision !== historyRevision" in load_history
    assert 'v-show="activeView === \'generate\'"' in source
    assert 'v-show="activeView === \'history\'"' in source


def test_history_view_has_complete_safe_ui_states() -> None:
    source = APP_VUE.read_text(encoding="utf-8")

    for visible_text in (
        "正在读取历史记录",
        "暂无历史记录",
        "历史记录读取失败",
        "重新加载",
        "刷新",
        "复制文案",
        "本功能限本地单用户使用",
    ):
        assert visible_text in source

    assert "item.image_summary" in source
    assert "item.title" in source
    assert "item.body" in source
    assert "item.tags" in source
    assert "item.created_at" in source
    assert "{{ item.generation_id }}" not in source
    assert "v-html" not in source

    for private_field in (
        "item.image_path",
        "item.user_input",
        "item.error_code",
        "item.error_message",
        "item.updated_at",
        "item.status",
    ):
        assert private_field not in source


def test_history_copy_contains_only_publishable_copy_fields() -> None:
    source = APP_VUE.read_text(encoding="utf-8")
    copy_generation = _function_body(source, "copyGeneration")

    assert "generation.title" in copy_generation
    assert "generation.body" in copy_generation
    assert "generation.tags.join(' ')" in copy_generation
    assert "generation.generation_id" not in copy_generation
    assert "generation.image_summary" not in copy_generation
