"""Static regression checks for the routed, account-scoped history page."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
APP_VUE = ROOT / "frontend" / "src" / "App.vue"
HISTORY_VIEW = ROOT / "frontend" / "src" / "views" / "HistoryView.vue"
WORKSPACE_STATE = ROOT / "frontend" / "src" / "state" / "workspace.ts"
GENERATION_SERVICE = ROOT / "frontend" / "src" / "services" / "generation.ts"


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


def test_history_service_uses_safe_preview_metadata_and_bounded_mutations() -> None:
    source = GENERATION_SERVICE.read_text(encoding="utf-8")
    delete_generation = _function_body(source, "realDeleteGeneration")

    assert "export interface GenerationHistoryItem extends GenerationResponse" in source
    assert "has_image_preview: boolean" in source
    assert "image_preview_url: string | null" in source
    assert "export interface GenerationHistoryResponse" in source
    assert "items: GenerationHistoryItem[]" in source
    assert "count: number" in source
    assert "`${API_BASE_URL}/api/v1/generations?limit=${encodeURIComponent(limit)}`" in source
    assert "method: 'GET'" in source
    assert "cache: 'no-store'" in source
    assert "limit < 1 || limit > 50" in source

    assert "`${API_BASE_URL}/api/v1/generations/${encodeURIComponent(generationId)}`" in delete_generation
    assert "method: 'DELETE'" in delete_generation
    assert "csrf: true" in delete_generation
    assert "expectNoContent: true" in delete_generation
    assert "isBoundedString(generationId, 1, 128)" in source


def test_history_preview_urls_are_consistent_and_restricted_to_the_api_origin() -> None:
    source = GENERATION_SERVICE.read_text(encoding="utf-8")
    parse_item = _function_body(source, "parseHistoryItem")
    parse_url = _function_body(source, "parseImagePreviewUrl")

    assert "typeof value.has_image_preview !== 'boolean'" in parse_item
    assert "value.image_preview_url" in parse_item
    assert "value.has_image_preview" in parse_item
    assert "if (!hasImagePreview)" in parse_url
    assert "if (value !== null)" in parse_url
    assert "isBoundedString(value, 1, 2048)" in parse_url
    assert "GENERATION_UUID_PATTERN.test(generationId)" in parse_url
    assert "new URL(API_BASE_URL, window.location.origin).origin" in parse_url
    assert "previewUrl.origin !== apiOrigin" in parse_url
    assert "previewUrl.protocol !== 'http:'" in parse_url
    assert "previewUrl.protocol !== 'https:'" in parse_url
    assert "`/api/v1/generations/${generationId}/image-preview`" in parse_url
    assert "previewUrl.pathname !== expectedPath" in parse_url
    assert "previewUrl.search !== ''" in parse_url
    assert "previewUrl.hash !== ''" in parse_url


def test_mock_history_creates_bounded_previews_and_releases_deleted_urls() -> None:
    source = GENERATION_SERVICE.read_text(encoding="utf-8")
    create_preview = _function_body(source, "createMockHistoryPreview")
    revoke_preview = _function_body(source, "revokeMockHistoryPreview")

    assert "createImageBitmap(value)" in create_preview
    assert "480 / Math.max(bitmap.width, bitmap.height)" in create_preview
    assert "canvas.toBlob(resolve, 'image/webp', 0.72)" in create_preview
    assert "preview.size > 256 * 1024" in create_preview
    assert "URL.createObjectURL(preview)" in create_preview
    assert "bitmap?.close()" in create_preview
    assert "URL.revokeObjectURL(item.image_preview_url)" in revoke_preview
    assert "revokeMockHistoryPreview(removed)" in source


def test_history_view_refreshes_from_the_shared_workspace_on_each_mount() -> None:
    app_source = APP_VUE.read_text(encoding="utf-8")
    view_source = HISTORY_VIEW.read_text(encoding="utf-8")
    workspace_source = WORKSPACE_STATE.read_text(encoding="utf-8")
    load_history = _function_body(app_source, "loadHistory")
    handle_generate = _function_body(app_source, "handleGenerate")

    assert "export type HistoryStatus = 'idle' | 'loading' | 'success' | 'error'" in workspace_source
    assert "const HISTORY_LIMIT = 20" in app_source
    assert "listGenerations(HISTORY_LIMIT)" in load_history
    assert "generate(" not in load_history
    assert "historyRevision += 1" in handle_generate
    assert "historyLoaded.value = false" in handle_generate
    assert "route.name === 'history'" in handle_generate
    assert "requestedRevision !== historyRevision" in load_history

    assert "useWorkspace()" in view_source
    mounted = re.search(
        r"onMounted\(\(\) => \{(.*?)\n\}\)", view_source, flags=re.DOTALL
    )
    assert mounted is not None
    assert "void loadHistory()" in mounted.group(1)
    assert "if (!historyLoaded.value)" not in mounted.group(1)

    assert "activeView" not in app_source
    assert 'v-show="activeView' not in app_source


def test_history_view_has_complete_safe_ui_states_and_page_semantics() -> None:
    source = HISTORY_VIEW.read_text(encoding="utf-8")

    for visible_text in (
        "正在读取历史记录",
        "暂无历史记录",
        "历史记录读取失败",
        "重新加载",
        "刷新",
        "复制文案",
        "载回工作台",
        "确认删除",
        "暂无图片预览",
        "记录只属于当前登录账号",
        "当前为免登录演示模式",
    ):
        assert visible_text in source

    assert source.count("<h1") == 1
    assert '<h1 id="page-title" tabindex="-1">' in source
    assert 'aria-labelledby="page-title"' in source
    assert "item.image_summary" in source
    assert "item.title" in source
    assert "item.body" in source
    assert "item.tags" in source
    assert "item.created_at" in source
    assert "item.has_image_preview" in source
    assert "item.image_preview_url" in source
    assert "{{ item.generation_id }}" not in source
    assert "v-html" not in source
    assert "aspect-ratio: 4 / 3" in source
    assert "aspect-ratio: 16 / 9" in source
    assert ":icon=\"Delete\"" in source
    assert "ElMessageBox.confirm(" in source
    assert source.index("ElMessageBox.confirm(") < source.index(
        "await deleteHistoryItem(item)"
    )

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


def test_history_restore_keeps_remote_preview_non_submittable_and_invalidates_generation() -> None:
    app_source = APP_VUE.read_text(encoding="utf-8")
    view_source = (ROOT / "frontend" / "src" / "views" / "GenerateView.vue").read_text(
        encoding="utf-8"
    )
    restore = _function_body(app_source, "restoreHistoryItem")

    assert restore.index("generationRequestId += 1") < restore.index("clearImage()")
    assert "Object.assign(result" in restore
    assert "tags: [...generation.tags]" in restore
    assert "imageFile.value = null" in restore
    assert "generation.image_preview_url ?? ''" in restore
    assert "restoredFromHistory.value = true" in restore
    assert "status.value = 'success'" in restore
    assert "await router.push({ name: 'generate' })" in restore

    assert "历史图片仅用于预览" in view_source
    assert "不会把它当作可再次提交的本地文件" in view_source
    assert ':disabled="!imageFile"' in view_source


def test_delete_installs_local_mutation_only_after_success_and_is_session_versioned() -> None:
    source = APP_VUE.read_text(encoding="utf-8")
    delete_item = _function_body(source, "deleteHistoryItem")

    assert "historyDeleteTokens.has(generationId)" in delete_item
    assert "requestedAuthRevision = authRevision.value" in delete_item
    assert "requestedUserId = currentUser.value?.user_id ?? null" in delete_item
    assert "requestedAuthRevision !== authRevision.value" in delete_item
    assert "requestedUserId !== (currentUser.value?.user_id ?? null)" in delete_item
    assert delete_item.index("await deleteGeneration(generationId)") < delete_item.index(
        "historyItems.value = historyItems.value.filter"
    )
    assert "historyRequestId += 1" in delete_item
    assert "historyRevision += 1" in delete_item
    assert "historyCount.value = historyItems.value.length" in delete_item

    failure_path = delete_item.split("} catch", 1)[1].split("} finally", 1)[0]
    assert "historyItems.value =" not in failure_path
    assert "historyCount.value =" not in failure_path
